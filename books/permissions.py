from functools import wraps
from django.db.models import Q
from django.http import HttpResponseForbidden
from .models import Role, UserProfile, UserRole, AuditLog


def has_librarian_permission(user) -> bool:
    """同時相容 UserProfile、UserRole 與 Django Group 的館員權限判斷（支援 code 與 name）"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    role_query = Q(role__code__in=["ADMIN", "LIBRARIAN"]) | Q(role__name__in=["Admin", "Librarian", "系統管理員", "館員"])
    in_profile = UserProfile.objects.filter(role_query, user=user).exists()
    in_custom_role = UserRole.objects.filter(role_query, user=user).exists()
    in_django_group = user.groups.filter(name__in=["Admin", "Librarian"]).exists()

    return in_profile or in_custom_role or in_django_group


def librarian_required(view_func):
    """館員權限裝飾器，並自動寫入稽核日誌"""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not has_librarian_permission(request.user):
            AuditLog.objects.create(
                user=request.user if request.user.is_authenticated else None,
                action="PERMISSION_DENIED",
                target=request.path,
                detail="嘗試執行館員專屬操作遭拒"
            )
            return HttpResponseForbidden("權限不足：僅限館員或管理員執行此操作。")
        return view_func(request, *args, **kwargs)
    return _wrapped_view