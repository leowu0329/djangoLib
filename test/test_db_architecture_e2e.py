import json
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.db.models import ProtectedError
from django.contrib.auth.models import User, Group
from books.models import (
    Book, BookCopy, LoanRecord, LoanItem, Reservation,
    Fine, Payment, Member, Branch, Shelf, Publisher, Category, Author, BookAuthor, AuditLog
)
from books.services import LibraryService, LibraryBusinessError


class DatabaseArchitectureAndE2ETest(TestCase):
    """
    資料庫架構設計（24張核心資料表）與四大關鍵設計原則（A/B/C/D）前後端整合測試：
    A. 書籍與副本分離 (Book vs BookCopy + Branch/Shelf/Publisher/Category/Author)
    B. 借閱紀錄不可直接刪除 (LoanRecord PROTECT + 覆寫 delete() 防護)
    C. 所有金額都要保留交易紀錄 (Fine 罰單 + Payment 繳費/折抵/退款流水帳)
    D. 避免重複借出 (交易鎖定與狀態原子性檢查，阻擋重複借閱)
    """

    def setUp(self):
        self.librarian_group, _ = Group.objects.get_or_create(name="Librarian")
        self.librarian = User.objects.create_user(username="lib_staff", password="Password123!")
        self.librarian.groups.add(self.librarian_group)

        self.reader_1 = User.objects.create_user(username="reader_wang", password="Password123!")
        self.member_1 = Member.objects.create(
            user=self.reader_1, member_code="M2026001", full_name="王小明", status=Member.Status.ACTIVE
        )

        self.reader_2 = User.objects.create_user(username="reader_lin", password="Password123!")
        self.member_2 = Member.objects.create(
            user=self.reader_2, member_code="M2026002", full_name="林小華", status=Member.Status.ACTIVE
        )

        self.branch = Branch.objects.create(code="MAIN", name="台南總館", address="台南市東區")
        self.shelf = Shelf.objects.create(branch=self.branch, code="A-03", floor="2F 資訊科學區")
        self.publisher = Publisher.objects.create(name="碁峰資訊", country="台灣")
        self.category = Category.objects.create(code="312.9", name="資料庫與系統架構")
        self.author = Author.objects.create(name="Martin Kleppmann")

        self.client = Client()

    def _assert_hx_modal(self, response, expected_level: str, expected_snippet: str):
        self.assertEqual(response.status_code, 200)
        self.assertIn("HX-Trigger", response.headers)
        payload = json.loads(response.headers["HX-Trigger"])["showModalAlert"]
        self.assertEqual(payload["level"], expected_level)
        self.assertIn(expected_snippet, payload["message"])

    def test_principle_a_book_and_copies_separation_frontend_backend(self):
        """測試原則 A：書籍與副本分離，館員透過前端新增書目並產生多本獨立實體副本"""
        self.client.login(username="lib_staff", password="Password123!")
        res = self.client.post(
            reverse("books:add_book"),
            data={
                "title": "資料密集型應用系統設計 (DDIA)",
                "isbn": "9789865021234",
                "author": "Martin Kleppmann",
                "publisher": "碁峰資訊",
                "category": "資料庫架構",
                "tags": "PostgreSQL,Distributed",
                "copy_count": 3,
                "location": "2F-A03"
            },
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(res, "success", "共 3 本副本")

        book = Book.objects.get(isbn="9789865021234")
        book.publisher_ref = self.publisher
        book.category_ref = self.category
        book.save()
        BookAuthor.objects.create(book=book, author=self.author, author_order=1)

        copies = list(book.copies.all())
        self.assertEqual(len(copies), 3)
        copies[0].branch = self.branch
        copies[0].shelf = self.shelf
        copies[0].save()

        self.assertEqual(len({c.barcode for c in copies}), 3)
        self.assertEqual(book.available_copies_count, 3)

    def test_principle_b_loan_records_immutable_and_protected(self):
        """測試原則 B：借閱紀錄不可直接刪除，且受關聯外鍵 PROTECT 保護歷史完整性"""
        book, copies = LibraryService.create_book_with_copies(
            title="PostgreSQL 實戰手冊", isbn="9789861112223", author="Bruce Momjian",
            publisher="歐萊禮", category="資料庫", tags="SQL", copy_count=1
        )
        copy = copies[0]
        loan = LibraryService.borrow_book(self.reader_1, copy.barcode)

        # 1. 直接呼叫 loan.delete() 必須拋出安全規範例外
        with self.assertRaises(ValueError):
            loan.delete()

        # 2. 嘗試刪除已產生借閱歷史的館藏副本或讀者帳號，必須觸發 ProtectedError
        with self.assertRaises(ProtectedError):
            copy.delete()

        with self.assertRaises(ProtectedError):
            self.reader_1.delete()

        # 3. 即使讀者停權、副本報廢，歷史借閱紀錄仍完整保留
        LibraryService.return_book(copy.barcode)
        self.member_1.status = Member.Status.SUSPENDED
        self.member_1.save()
        copy.status = BookCopy.Status.RETIRED
        copy.save()

        preserved_loan = LoanRecord.objects.get(id=loan.id)
        self.assertEqual(preserved_loan.status, LoanRecord.Status.RETURNED)
        self.assertEqual(preserved_loan.user.username, "reader_wang")

    def test_principle_c_financial_ledger_transactions(self):
        """測試原則 C：所有金額皆保留獨立交易紀錄（開立罰單 -> 部分繳費 -> 折抵減免 -> 恢復借閱）"""
        book, copies = LibraryService.create_book_with_copies(
            title="資料庫系統概論", isbn="9789570001112", author="C.J. Date",
            publisher="全華", category="資料庫", tags="DB", copy_count=1
        )
        copy = copies[0]

        loan = LibraryService.borrow_book(self.reader_1, copy.barcode)
        loan.due_date = timezone.now() - timedelta(days=6)
        loan.save()

        self.client.login(username="reader_wang", password="Password123!")
        res_ret = self.client.post(
            reverse("books:return_copy"),
            data={"barcode": copy.barcode},
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(res_ret, "warning", "產生罰金 NT$ 30.00 元")

        fine = Fine.objects.get(loan=loan)
        self.assertEqual(fine.amount, Decimal("30.00"))
        self.assertEqual(fine.status, Fine.Status.UNPAID)

        with self.assertRaises(LibraryBusinessError):
            LibraryService.borrow_book(self.reader_1, copy.barcode)

        # 交易 1：現金繳納 20 元 (PARTIAL)
        tx1 = LibraryService.record_fine_payment(
            fine.id, Decimal("20.00"), Payment.TransactionType.PAYMENT, operator=self.librarian
        )
        fine.refresh_from_db()
        self.assertEqual(fine.status, Fine.Status.PARTIAL)

        # 交易 2：館員折抵減免 10 元 (PAID)
        tx2 = LibraryService.record_fine_payment(
            fine.id, Decimal("10.00"), Payment.TransactionType.WAIVER, operator=self.librarian
        )
        fine.refresh_from_db()
        self.assertEqual(fine.status, Fine.Status.PAID)

        self.assertEqual(Payment.objects.filter(fine=fine).count(), 2)
        self.assertNotEqual(tx1.reference_no, tx2.reference_no)
        new_loan = LibraryService.borrow_book(self.reader_1, copy.barcode)
        self.assertEqual(new_loan.status, LoanRecord.Status.ACTIVE)

    def test_principle_d_prevent_duplicate_borrowing_and_frontend_flow(self):
        """測試原則 D：避免重複借出（第一位讀者借出後，第二位讀者透過前端與後端同時借閱同一條碼必須被擋下）"""
        book, copies = LibraryService.create_book_with_copies(
            title="交易處理與並發控制", isbn="9789867778889", author="Jim Gray",
            publisher="麥格羅希爾", category="分散式系統", tags="Transaction", copy_count=1
        )
        copy = copies[0]

        # 讀者 1 透過前端 HTMX 借出成功
        self.client.login(username="reader_wang", password="Password123!")
        res1 = self.client.post(
            reverse("books:borrow_copy"),
            data={"barcode": copy.barcode},
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(res1, "success", "借閱完成")

        # 讀者 2 嘗試透過前端 HTMX 借出同一本副本（重複借出防禦）
        self.client.login(username="reader_lin", password="Password123!")
        res2 = self.client.post(
            reverse("books:borrow_copy"),
            data={"barcode": copy.barcode},
            HTTP_HX_REQUEST="true"
        )
        self._assert_hx_modal(res2, "danger", "已借出")

        # 後端服務層直接呼叫同樣必須拋出業務例外
        with self.assertRaises(LibraryBusinessError):
            LibraryService.borrow_book(self.reader_2, copy.barcode)

        self.assertEqual(LoanRecord.objects.filter(copy=copy, status=LoanRecord.Status.ACTIVE).count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="BORROW_BOOK", target=copy.barcode).exists())
