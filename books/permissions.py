from functools import wraps
from django.apps import apps
from django.http import HttpResponseForbidden


def user_has_permission(user, perm_code: str) -> bool:
    """
    檢查使用者是否具備指定的 RBAC 權限代碼。
    自動相容 direct ManyToMany、UserRole 中間表、以及 is_staff / is_superuser。
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    # 1. 檢查直接的多對多關聯 (user.roles)
    if hasattr(user, 'roles'):
        if user.roles.filter(permissions__code=perm_code).exists() or user.roles.exists() and user.is_staff:
            return True

    # 2. 動態掃描 books App 中的 UserRole / Profile 關聯表
    for model in apps.get_app_config('books').get_models():
        field_names = {f.name for f in model._meta.get_fields()}
        if 'user' in field_names and 'role' in field_names:
            qs = model.objects.filter(user=user)
            if qs.filter(role__permissions__code=perm_code).exists():
                return True
            # 若為系統管理員或館員角色
            if qs.filter(role__name__iregex=r'(admin|librarian|管理|館員|流通)').exists():
                return True

    # 3. 退回檢查 Django 內建權限
    return user.has_perm(perm_code)


def require_permission(perm_code: str):
    """視圖權限驗證裝飾器：權限不足時回傳 HTTP 403 Forbidden"""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not user_has_permission(request.user, perm_code):
                return HttpResponseForbidden(f"權限不足：需要 '{perm_code}' 權限才能執行此操作。")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator