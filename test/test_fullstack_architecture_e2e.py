import json
from django.apps import apps
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.management import call_command
from books import models as book_models

User = get_user_model()


def resolve_model(*candidates):
    """從 books.models 中依序尋找實際存在的資料模型類別"""
    for name in candidates:
        model = getattr(book_models, name, None)
        if model is not None:
            return model
    for model in apps.get_app_config('books').get_models():
        if any(k in model.__name__.lower() for k in ('loan', 'borrow', 'circulation', 'record')):
            return model
    return None


class FullstackArchitectureE2ETest(TestCase):
    """
    前後端整合端到端測試 (Fullstack HTMX + Django + SQLite3 E2E Test)
    """

    @classmethod
    def setUpTestData(cls):
        # 1. 初始化專案內建的 6 大 RBAC 角色、9 項權限與 demo_* 帳號
        call_command('init_rbac')

        # 2. 動態解析專案實際的模型類別
        cls.Book = resolve_model('Book')
        cls.BookCopy = resolve_model('BookCopy', 'Copy', 'Item')
        cls.Branch = resolve_model('Branch', 'LibraryBranch')
        cls.Category = resolve_model('Category')
        cls.Author = resolve_model('Author')
        cls.Role = resolve_model('Role')

        # 3. 建立測試帳號並綁定具備流通權限的館員角色
        cls.librarian = User.objects.create_user(
            username='librarian_admin',
            password='TestPassword123!',
            is_staff=True
        )
        if cls.Role is not None:
            librarian_role = (
                cls.Role.objects.filter(name__iregex=r'(librarian|circulation|admin|館員|流通|管理)').first()
                or cls.Role.objects.first()
            )
            if librarian_role:
                if hasattr(cls.librarian, 'roles'):
                    cls.librarian.roles.add(librarian_role)
                # 同步寫入 UserRole 中間表（若存在）
                for model in apps.get_app_config('books').get_models():
                    field_names = {f.name for f in model._meta.get_fields()}
                    if 'user' in field_names and 'role' in field_names:
                        model.objects.get_or_create(user=cls.librarian, role=librarian_role)

        cls.guest_user = User.objects.create_user(
            username='guest_reader',
            password='TestPassword123!'
        )

        # 4. 建立基礎館藏測試資料
        book_kwargs = {'title': 'The Art of Computer Programming'}
        book_fields = {f.name for f in cls.Book._meta.get_fields()}

        if 'isbn' in book_fields:
            book_kwargs['isbn'] = '9780201896831'

        if cls.Category and 'category' in book_fields:
            cat_fields = {f.name for f in cls.Category._meta.get_fields()}
            cat_kwargs = {'name': '資訊科學'}
            if 'code' in cat_fields:
                cat_kwargs['code'] = '005'
            cls.category = cls.Category.objects.create(**cat_kwargs)
            book_kwargs['category'] = cls.category

        cls.book = cls.Book.objects.create(**book_kwargs)

        if cls.Author and 'authors' in book_fields:
            cls.author = cls.Author.objects.create(name='Donald Knuth')
            cls.book.authors.add(cls.author)

        copy_fields = {f.name for f in cls.BookCopy._meta.get_fields()}
        copy_kwargs = {'book': cls.book}

        if 'barcode' in copy_fields:
            copy_kwargs['barcode'] = 'BC-2026-0001'
        if 'status' in copy_fields:
            copy_kwargs['status'] = 'AVAILABLE'
        if cls.Branch and 'branch' in copy_fields:
            branch_fields = {f.name for f in cls.Branch._meta.get_fields()}
            branch_kwargs = {'name': '台南東區總館'}
            if 'code' in branch_fields:
                branch_kwargs['code'] = 'TN-EAST'
            cls.branch = cls.Branch.objects.create(**branch_kwargs)
            copy_kwargs['branch'] = cls.branch

        cls.copy = cls.BookCopy.objects.create(**copy_kwargs)

    def setUp(self):
        self.client = Client()

    def test_01_unauthenticated_redirect_to_login(self):
        """測試未登入狀態存取館藏首頁，應重導向至登入畫面"""
        response = self.client.get(reverse('books:book_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_02_full_page_load_and_ui_assets(self):
        """測試館員登入後首次載入完整頁面，確認載入 HTMX、Bootstrap 5 與 Noto Sans TC 字體"""
        login_success = self.client.login(username='librarian_admin', password='TestPassword123!')
        self.assertTrue(login_success)

        response = self.client.get(reverse('books:book_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')

        self.assertIn('htmx.org', html)
        self.assertIn('bootstrap@5.3.3', html)
        self.assertIn('Noto+Sans+TC', html)
        self.assertIn('id="book-list-container"', html)
        self.assertIn('The Art of Computer Programming', html)

    def test_03_htmx_partial_search_by_barcode(self):
        """模擬前端輸入框觸發 HTMX 條碼搜尋，後端僅回傳局部 HTML 片段而非完整導覽列"""
        self.client.login(username='librarian_admin', password='TestPassword123!')

        response = self.client.get(
            reverse('books:book_list'),
            data={'q': 'BC-2026-0001'},
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        partial_html = response.content.decode('utf-8')

        self.assertIn('BC-2026-0001', partial_html)
        self.assertNotIn('<nav class="navbar', partial_html)

    def test_04_fullstack_borrow_and_return_workflow(self):
        """完整前後端循環測試：HTMX 借書請求 -> 驗證資料庫與 Modal 觸發 -> HTMX 還書請求"""
        self.client.login(username='librarian_admin', password='TestPassword123!')

        # Step A: 模擬前端點擊借書按鈕發送 HTMX POST
        borrow_url = reverse('books:borrow_copy', kwargs={'copy_id': self.copy.id})
        borrow_res = self.client.post(borrow_url, HTTP_HX_REQUEST='true')
        self.assertEqual(borrow_res.status_code, 200)

        # 驗證後端回傳 HX-Trigger 通知前端開啟 Bootstrap Modal
        self.assertIn('HX-Trigger', borrow_res.headers)
        trigger_data = json.loads(borrow_res.headers['HX-Trigger'])
        self.assertTrue(trigger_data.get('showNotificationModal'))

        # 驗證資料庫狀態已更新
        self.copy.refresh_from_db()
        if hasattr(self.copy, 'status'):
            self.assertNotEqual(self.copy.status, 'AVAILABLE')

        # Step B: 模擬前端點擊還書按鈕發送 HTMX POST
        return_url = reverse('books:return_copy', kwargs={'copy_id': self.copy.id})
        return_res = self.client.post(return_url, HTTP_HX_REQUEST='true')
        self.assertEqual(return_res.status_code, 200)

        # 驗證資料庫狀態已恢復為可借閱 (AVAILABLE)
        self.copy.refresh_from_db()
        if hasattr(self.copy, 'status'):
            self.assertEqual(self.copy.status, 'AVAILABLE')

    def test_05_unauthorized_user_blocked_by_rbac(self):
        """驗證無流通權限的使用者發送 HTMX 借書請求時，遭後端 RBAC 攔截並回傳 403"""
        self.client.login(username='guest_reader', password='TestPassword123!')
        borrow_url = reverse('books:borrow_copy', kwargs={'copy_id': self.copy.id})
        response = self.client.post(borrow_url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 403)