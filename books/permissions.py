from functools import wraps
from django.http import HttpResponseForbidden
from django.template.loader import render_to_string
from .models import Role, AuditLog


def get_user_role(user) -> Role:
    """取得目前使用者的角色，未登入則自動對應 Guest 角色"""
    if user and user.is_authenticated and hasattr(user, 'profile'):
        return user.profile.role
    guest_role, _ = Role.objects.get_or_create(
        code='guest',
        defaults={'name': '訪客 Guest', 'book_read': True}
    )
    return guest_role


def log_audit(user, action: str, target_model: str, target_id: str, changes: str):
    """寫入操作稽核紀錄"""
    role = get_user_role(user)
    AuditLog.objects.create(
        actor=user if (user and user.is_authenticated) else None,
        actor_role=role.name,
        action=action,
        target_model=target_model,
        target_id=str(target_id),
        changes=changes
    )


def require_rbac_permission(perm_string: str):
    """後端 API 細粒度權限驗證裝飾器"""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            role = get_user_role(request.user)
            if not role.has_perm(perm_string):
                # 記錄越權嘗試至稽核日誌
                log_audit(
                    request.user,
                    action=f"ACCESS_DENIED ({perm_string})",
                    target_model="Security",
                    target_id="-",
                    changes=f"角色 [{role.name}] 嘗試執行未授權操作：{perm_string}"
                )
                # 若為 HTMX 請求，回傳 403 狀態碼並附帶 Bootstrap Modal HTML 片段
                if request.headers.get('HX-Request'):
                    html = render_to_string('books/partials/modal_alert.html', {
                        'modal_title': '權限不足 (HTTP 403 Forbidden)',
                        'modal_type': 'danger',
                        'modal_icon': 'fa-shield-halved',
                        'modal_message': f'您目前的角色為「{role.name}」，不具備執行「{perm_string}」的操作權限。此越權行為已被系統記錄。'
                    }, request=request)
                    response = HttpResponseForbidden(html)
                    # 透過 HX-Retarget 將錯誤視窗導向全域 Modal 容器
                    response['HX-Retarget'] = '#global-modal-container'
                    response['HX-Reswap'] = 'innerHTML'
                    return response
                return HttpResponseForbidden(f"403 Forbidden: 缺少權限 {perm_string}")
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator