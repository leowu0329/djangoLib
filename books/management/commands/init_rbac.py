from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from books.models import Role, Permission, RolePermission, UserProfile


class Command(BaseCommand):
    help = "初始化圖書館 RBAC 六大角色、權限矩陣 (Permission / RolePermission) 與測試帳號"

    def handle(self, *args, **options):
        permission_defs = {
            "book_read": "查詢書目與館藏",
            "book_create": "新增書目與館藏入庫",
            "book_update": "編輯書目與館藏狀態",
            "book_delete": "刪除或報廢書目",
            "borrow_create": "辦理書籍借閱",
            "borrow_return": "辦理書籍歸還",
            "borrow_approve": "審核特殊借閱與罰金折抵",
            "procurement_manage": "管理採購清單",
            "system_settings": "管理系統全域參數",
        }

        perm_objects = {}
        for p_code, p_name in permission_defs.items():
            perm_obj, _ = Permission.objects.update_or_create(
                code=p_code, defaults={"name": p_name}
            )
            perm_objects[p_code] = perm_obj

        roles_matrix = [
            {
                "code": "super_admin",
                "name": "超級管理員 Super Admin",
                "perms": list(permission_defs.keys()),
            },
            {
                "code": "librarian_admin",
                "name": "館長 Librarian Admin",
                "perms": [
                    "book_read", "book_create", "book_update", "book_delete",
                    "borrow_create", "borrow_return", "borrow_approve", "procurement_manage",
                ],
            },
            {
                "code": "librarian",
                "name": "圖書館員 Librarian",
                "perms": [
                    "book_read", "book_create", "book_update",
                    "borrow_create", "borrow_return", "borrow_approve",
                ],
            },
            {
                "code": "procurement",
                "name": "採購人員 Procurement",
                "perms": ["book_read", "book_create", "book_update", "procurement_manage"],
            },
            {
                "code": "member",
                "name": "讀者 Member",
                "perms": ["book_read", "borrow_create"],
            },
            {
                "code": "guest",
                "name": "訪客 Guest",
                "perms": ["book_read"],
            },
        ]

        for r_data in roles_matrix:
            role, _ = Role.objects.update_or_create(
                code=r_data["code"],
                defaults={
                    "name": r_data["name"],
                    "permissions": r_data["perms"],
                },
            )
            # 同步寫入 RolePermission 關聯表
            RolePermission.objects.filter(role=role).delete()
            for p_code in r_data["perms"]:
                RolePermission.objects.create(role=role, permission=perm_objects[p_code])

            username = f"demo_{r_data['code']}"
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password("pass1234")
                user.save()
            UserProfile.objects.update_or_create(user=user, defaults={"role": role})

        self.stdout.write(
            self.style.SUCCESS("成功初始化 6 大 RBAC 角色、9 項正規化權限與 demo_* 測試帳號！")
        )