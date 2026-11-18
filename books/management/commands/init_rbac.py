from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from books.models import Role, UserProfile


class Command(BaseCommand):
    help = '初始化圖書館 RBAC 六大角色權限矩陣與測試帳號'

    def handle(self, *args, **options):
        roles_matrix = [
            {
                'code': 'super_admin', 'name': '超級管理員 Super Admin',
                'book_read': True, 'book_create': True, 'book_update': True, 'book_delete': True,
                'borrow_create': True, 'borrow_return': True, 'borrow_approve': True,
                'procurement_manage': True, 'system_settings': True,
            },
            {
                'code': 'librarian_admin', 'name': '館長 Librarian Admin',
                'book_read': True, 'book_create': True, 'book_update': True, 'book_delete': True,
                'borrow_create': True, 'borrow_return': True, 'borrow_approve': True,
                'procurement_manage': True, 'system_settings': False,
            },
            {
                'code': 'librarian', 'name': '圖書館員 Librarian',
                'book_read': True, 'book_create': True, 'book_update': True, 'book_delete': False,
                'borrow_create': True, 'borrow_return': True, 'borrow_approve': True,
                'procurement_manage': False, 'system_settings': False,
            },
            {
                'code': 'procurement', 'name': '採購人員 Procurement',
                'book_read': True, 'book_create': True, 'book_update': True, 'book_delete': False,
                'borrow_create': False, 'borrow_return': False, 'borrow_approve': False,
                'procurement_manage': True, 'system_settings': False,
            },
            {
                'code': 'member', 'name': '讀者 Member',
                'book_read': True, 'book_create': False, 'book_update': False, 'book_delete': False,
                'borrow_create': True, 'borrow_return': False, 'borrow_approve': False,
                'procurement_manage': False, 'system_settings': False,
            },
            {
                'code': 'guest', 'name': '訪客 Guest',
                'book_read': True, 'book_create': False, 'book_update': False, 'book_delete': False,
                'borrow_create': False, 'borrow_return': False, 'borrow_approve': False,
                'procurement_manage': False, 'system_settings': False,
            },
        ]

        for r_data in roles_matrix:
            role, _ = Role.objects.update_or_create(code=r_data['code'], defaults=r_data)
            username = f"demo_{r_data['code']}"
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password('pass1234')
                user.save()
            UserProfile.objects.update_or_create(user=user, defaults={'role': role})

        self.stdout.write(self.style.SUCCESS('成功初始化 6 大 RBAC 角色與 demo_* 測試帳號！'))