from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Book, BorrowRecord, Role, AuditLog
from .permissions import require_rbac_permission, get_user_role, log_audit


def _render_book_list_with_modal(request, modal_context=None):
    """輔助函式：回傳更新後的書籍列表，並可透過 OOB (Out-Of-Band) 同時觸發 Bootstrap Modal"""
    books = Book.objects.all().order_by('-created_at')
    role = get_user_role(request.user)
    recent_logs = AuditLog.objects.all()[:8]
    context = {
        'books': books,
        'current_role': role,
        'recent_logs': recent_logs,
        'modal': modal_context,
    }
    return render(request, 'books/partials/book_list.html', context)


@require_rbac_permission('book.read')
def book_list(request):
    query = request.GET.get('q', '').strip()
    books = Book.objects.all().order_by('-created_at')
    if query:
        books = books.filter(
            Q(title__icontains=query) | Q(author__icontains=query) | Q(isbn__icontains=query)
        )

    role = get_user_role(request.user)
    all_roles = Role.objects.all()
    recent_logs = AuditLog.objects.all()[:8]

    context = {
        'books': books,
        'query': query,
        'current_role': role,
        'all_roles': all_roles,
        'recent_logs': recent_logs,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'books/partials/book_list.html', context)
    return render(request, 'books/base.html', context)


def switch_role(request, role_code):
    """快速切換展示角色（方便體驗不同 RBAC 權限視角）"""
    if role_code == 'guest':
        from django.contrib.auth import logout
        logout(request)
    else:
        user = get_object_or_404(User, username=f"demo_{role_code}")
        login(request, user)
    return redirect('book_list')


@require_POST
@require_rbac_permission('book.create')
def add_book(request):
    title = request.POST.get('title', '').strip()
    author = request.POST.get('author', '').strip()
    isbn = request.POST.get('isbn', '').strip()
    cover = request.FILES.get('cover')

    if title and author and isbn:
        book = Book.objects.create(title=title, author=author, isbn=isbn, cover=cover)
        log_audit(
            request.user, 'CREATE_BOOK', 'Book', book.id,
            f"新增書籍：《{book.title}》 (ISBN: {book.isbn})"
        )
        return _render_book_list_with_modal(request, {
            'title': '新增館藏成功',
            'type': 'success',
            'icon': 'fa-circle-check',
            'message': f'書籍《{book.title}》已成功登錄至館藏系統並寫入稽核日誌。'
        })
    return _render_book_list_with_modal(request)


@require_POST
@require_rbac_permission('book.update')
def update_book(request, book_id):
    book = get_object_or_404(Book, id=book_id)
    old_title = book.title
    new_title = request.POST.get('title', '').strip()
    new_author = request.POST.get('author', '').strip()

    if new_title and new_author:
        book.title = new_title
        book.author = new_author
        book.save()
        log_audit(
            request.user, 'UPDATE_BOOK', 'Book', book.id,
            f"修改書籍資料：原書名「{old_title}」變更為「{book.title}」，作者「{book.author}」"
        )
        return _render_book_list_with_modal(request, {
            'title': '書籍更新成功',
            'type': 'primary',
            'icon': 'fa-pen-to-square',
            'message': f'書籍《{book.title}》資料已更新。'
        })
    return _render_book_list_with_modal(request)


@require_POST
@require_rbac_permission('book.delete')
def delete_book(request, book_id):
    book = get_object_or_404(Book, id=book_id)
    title = book.title
    book.delete()
    log_audit(
        request.user, 'DELETE_BOOK', 'Book', book_id,
        f"刪除館藏書籍：《{title}》"
    )
    return _render_book_list_with_modal(request, {
        'title': '書籍已刪除',
        'type': 'warning',
        'icon': 'fa-trash-can',
        'message': f'館藏書籍《{title}》已從資料庫移除。'
    })


@require_POST
@require_rbac_permission('borrow.create')
def borrow_book(request, book_id):
    book = get_object_or_404(Book, id=book_id)
    borrower_name = request.POST.get('borrower_name', '').strip()

    if book.is_available and borrower_name:
        book.is_available = False
        book.save()
        BorrowRecord.objects.create(
            book=book,
            borrower_name=borrower_name,
            status='borrowed',
            approved_by=request.user if request.user.is_authenticated else None
        )
        log_audit(
            request.user, 'BORROW_BOOK', 'BorrowRecord', book.id,
            f"借出書籍：《{book.title}》給讀者「{borrower_name}」"
        )
        return _render_book_list_with_modal(request, {
            'title': '借閱登記完成',
            'type': 'success',
            'icon': 'fa-book-reader',
            'message': f'已成功將《{book.title}》借出給「{borrower_name}」。'
        })
    return _render_book_list_with_modal(request)


@require_POST
@require_rbac_permission('borrow.return')
def return_book(request, book_id):
    book = get_object_or_404(Book, id=book_id)
    if not book.is_available:
        book.is_available = True
        book.save()
        record = book.records.filter(returned_at__isnull=True).last()
        borrower = record.borrower_name if record else '未知'
        if record:
            record.returned_at = timezone.now()
            record.status = 'returned'
            record.save()
        log_audit(
            request.user, 'RETURN_BOOK', 'BorrowRecord', book.id,
            f"歸還書籍：《{book.title}》（原借閱人：{borrower}）"
        )
        return _render_book_list_with_modal(request, {
            'title': '書籍歸還完成',
            'type': 'info',
            'icon': 'fa-rotate-left',
            'message': f'《{book.title}》已完成歸還驗收，重新開放借閱。'
        })
    return _render_book_list_with_modal(request)


@require_rbac_permission('system.settings')
def system_settings(request):
    """系統核心設定端點（僅 Super Admin 具備 system.settings=True）"""
    return _render_book_list_with_modal(request, {
        'title': '系統全域設定',
        'type': 'dark',
        'icon': 'fa-gears',
        'message': '歡迎進入超級管理員專屬系統設定面板。'
    })
