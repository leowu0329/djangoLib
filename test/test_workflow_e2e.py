import json
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User, Group
from books.models import Book, BookCopy, LoanRecord, Reservation
from books.services import LibraryService, LibraryBusinessError


class LibraryFullWorkflowE2ETest(TestCase):
    """完整圖書館業務流程（前端HTMX視圖互動 + 後端服務邏輯）端對端整合測試"""

    def setUp(self):
        # 1. 建立 RBAC 角色群組與測試帳號（1位館員、3位讀者）
        self.librarian_group, _ = Group.objects.get_or_create(name="Librarian")
        self.librarian = User.objects.create_user(username="admin_lib", password="Password123!")
        self.librarian.groups.add(self.librarian_group)

        self.reader_a = User.objects.create_user(username="reader_alice", password="Password123!")
        self.reader_b = User.objects.create_user(username="reader_bob", password="Password123!")
        self.reader_c = User.objects.create_user(username="reader_charlie", password="Password123!")

        self.client = Client()

    def _assert_hx_modal(self, response, expected_level: str, expected_text_snippet: str):
        """輔助驗證：確認後端回傳正確的 HTTP 200 與 HX-Trigger Modal 訊息"""
        self.assertEqual(response.status_code, 200)
        self.assertIn("HX-Trigger", response.headers)
        trigger_data = json.loads(response.headers["HX-Trigger"])
        modal_info = trigger_data["showModalAlert"]
        self.assertEqual(modal_info["level"], expected_level)
        self.assertIn(expected_text_snippet, modal_info["message"])

    def test_01_book_and_copies_decoupling_via_frontend(self):
        """測試流程 1：館員透過前端表單新增書目並產生 2 本獨立條碼副本"""
        self.client.login(username="admin_lib", password="Password123!")
        response = self.client.post(
            reverse("books:add_book"),
            data={
                "title": "JavaScript 完整學習指南",
                "isbn": "9789861234567",
                "author": "David Flanagan",
                "publisher": "歐萊禮",
                "category": "程式設計",
                "tags": "JS,Frontend",
                "copy_count": 2,
                "location": "資訊區-B2"
            },
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(response, "success", "共 2 本副本")

        # 驗證資料庫：1 筆 Book 對應 2 筆獨立條碼的 BookCopy
        self.assertEqual(Book.objects.count(), 1)
        book = Book.objects.first()
        self.assertEqual(book.copies.count(), 2)
        barcodes = list(book.copies.values_list("barcode", flat=True))
        self.assertNotEqual(barcodes[0], barcodes[1])

    def test_02_borrow_overdue_and_return_workflow(self):
        """測試流程 2：借閱流程、逾期天數判斷與逾期罰金計算"""
        book, copies = LibraryService.create_book_with_copies(
            title="Python 深度學習", isbn="9780001112223", author="Chollet", publisher="博碩",
            category="AI", tags="Python", copy_count=1
        )
        copy = copies[0]

        # 讀者 Alice 登入並透過前端介面掃描條碼借閱
        self.client.login(username="reader_alice", password="Password123!")
        res_borrow = self.client.post(
            reverse("books:borrow_copy"),
            data={"barcode": copy.barcode},
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(res_borrow, "success", "借閱完成")

        copy.refresh_from_db()
        self.assertEqual(copy.status, BookCopy.Status.BORROWED)

        # 模擬時間快轉：將該筆借閱應還日設為 4 天前（逾期 4 天，每日 5 元 = 20 元）
        loan = LoanRecord.objects.get(copy=copy, status=LoanRecord.Status.ACTIVE)
        loan.due_date = timezone.now() - timedelta(days=4)
        loan.save()

        # 執行歸還並驗證罰金計算
        res_return = self.client.post(
            reverse("books:return_copy"),
            data={"barcode": copy.barcode},
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(res_return, "warning", "產生罰金 NT$ 20.00 元")

        copy.refresh_from_db()
        self.assertEqual(copy.status, BookCopy.Status.AVAILABLE)

    def test_03_reservation_queue_expiration_and_damage_handling(self):
        """測試流程 3：預約候補順位、防重複預約、取書逾期遞補與損壞轉移機制"""
        book, copies = LibraryService.create_book_with_copies(
            title="Rust 系統程式設計", isbn="9789998887776", author="Jim Blandy", publisher="歐萊禮",
            category="系統架構", tags="Rust", copy_count=2
        )
        copy_1, copy_2 = copies[0], copies[1]

        # 1. 尚有在館書時，不允許預約（應直接借閱）
        self.client.login(username="reader_bob", password="Password123!")
        res_early = self.client.post(reverse("books:reserve_book", args=[book.id]), HTTP_HX_REQUEST="true")
        self._assert_hx_modal(res_early, "danger", "尚有在館可借副本")

        # 2. 讀者 Alice 將 2 本副本全數借走，使館藏可用數歸零
        LibraryService.borrow_book(self.reader_a, copy_1.barcode)
        LibraryService.borrow_book(self.reader_a, copy_2.barcode)

        # 3. 讀者 Bob 與 Charlie 依序預約，並驗證 Bob 無法重複預約
        res_bob = self.client.post(reverse("books:reserve_book", args=[book.id]), HTTP_HX_REQUEST="true")
        self._assert_hx_modal(res_bob, "success", "第 1 位")

        res_bob_dup = self.client.post(reverse("books:reserve_book", args=[book.id]), HTTP_HX_REQUEST="true")
        self._assert_hx_modal(res_bob_dup, "danger", "請勿重複預約")

        self.client.login(username="reader_charlie", password="Password123!")
        res_charlie = self.client.post(reverse("books:reserve_book", args=[book.id]), HTTP_HX_REQUEST="true")
        self._assert_hx_modal(res_charlie, "success", "第 2 位")

        # 4. Alice 歸還第 1 本副本 -> 系統自動將 copy_1 狀態設為 RESERVED 並通知第一順位 Bob
        LibraryService.return_book(copy_1.barcode)
        copy_1.refresh_from_db()
        self.assertEqual(copy_1.status, BookCopy.Status.RESERVED)

        bob_res = Reservation.objects.get(user=self.reader_b, book=book)
        self.assertEqual(bob_res.status, Reservation.Status.READY)
        self.assertEqual(bob_res.assigned_copy, copy_1)

        # 5. 非預約者 Charlie 嘗試搶先借閱 copy_1 -> 必須被系統阻擋
        with self.assertRaises(LibraryBusinessError):
            LibraryService.borrow_book(self.reader_c, copy_1.barcode)

        # 6. 模擬 Bob 超過 3 天未取書 -> 執行排程清理，自動取消 Bob 並遞補給 Charlie
        bob_res.pickup_deadline = timezone.now() - timedelta(minutes=10)
        bob_res.save()
        expired_count = LibraryService.process_expired_reservations()
        self.assertEqual(expired_count, 1)

        bob_res.refresh_from_db()
        charlie_res = Reservation.objects.get(user=self.reader_c, book=book)
        self.assertEqual(bob_res.status, Reservation.Status.CANCELLED)
        self.assertEqual(charlie_res.status, Reservation.Status.READY)
        self.assertEqual(charlie_res.assigned_copy, copy_1)

        # 7. 館藏損壞處理測試：Alice 歸還 copy_2（變為在館），此時若 copy_1 發生損壞，系統應自動將 Charlie 的保留轉移至 copy_2
        LibraryService.return_book(copy_2.barcode)
        self.client.login(username="admin_lib", password="Password123!")
        res_damage = self.client.post(
            reverse("books:report_damaged"),
            data={"barcode": copy_1.barcode},
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(res_damage, "warning", f"自動轉移至備用副本：{copy_2.barcode}")

        charlie_res.refresh_from_db()
        self.assertEqual(charlie_res.assigned_copy, copy_2)
