import inspect
import json
from django.db import transaction
from django.db.models import Q, ForeignKey, ManyToManyField
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from .models import Book, BookCopy
from .services import LibraryService
from .permissions import require_permission


def is_htmx(request):
    """判斷是否為前端 HTMX 發起的非同步局部請求"""
    return request.headers.get('HX-Request') == 'true'


def get_books_queryset(query=''):
    """動態偵測 Book 模型合法關聯屬性並建立安全查詢集"""
    fk_fields = []
    m2m_fields = []
    book_field_names = set()

    for field in Book._meta.get_fields():
        book_field_names.add(field.name)
        if isinstance(field, ForeignKey):
            fk_fields.append(field.name)
        elif isinstance(field, ManyToManyField):
            m2m_fields.append(field.name)
        elif field.auto_created and not field.concrete:
            accessor = field.get_accessor_name()
            if accessor and hasattr(Book, accessor):
                m2m_fields.append(accessor)

    qs = Book.objects.all()
    if fk_fields:
        qs = qs.select_related(*fk_fields)
    if m2m_fields:
        qs = qs.prefetch_related(*m2m_fields)

    if query:
        q_filter = Q(title__icontains=query)
        if 'isbn' in book_field_names:
            q_filter |= Q(isbn__icontains=query)

        copy_field_names = {f.name for f in BookCopy._meta.get_fields()}
        if 'barcode' in copy_field_names:
            if 'copies' in book_field_names:
                q_filter |= Q(copies__barcode__icontains=query)
            else:
                q_filter |= Q(bookcopy__barcode__icontains=query)
        qs = qs.filter(q_filter).distinct()

    return qs


def _invoke_library_service(action_keywords, user, target_obj):
    """透過 inspect 自動尋找並呼叫 LibraryService 中對應的業務方法"""
    for attr_name in dir(LibraryService):
        if attr_name.startswith('_'):
            continue
        if any(kw in attr_name.lower() for kw in action_keywords):
            func = getattr(LibraryService, attr_name)
            if callable(func):
                sig = inspect.signature(func)
                call_kwargs = {}
                for param_name in sig.parameters:
                    if param_name in ('copy', 'book_copy', 'item', 'book'):
                        call_kwargs[param_name] = target_obj
                    elif param_name in ('copy_id', 'book_copy_id', 'book_id'):
                        call_kwargs[param_name] = target_obj.id
                    elif param_name in ('user', 'borrower', 'reader', 'member', 'operator', 'staff'):
                        call_kwargs[param_name] = user
                    elif param_name in ('user_id', 'borrower_id', 'reader_id'):
                        call_kwargs[param_name] = user.id
                    elif param_name == 'barcode' and hasattr(target_obj, 'barcode'):
                        call_kwargs[param_name] = target_obj.barcode
                return func(**call_kwargs)
    return None


@login_required
@require_http_methods(["GET"])
def book_list_view(request):
    """館藏查詢主畫面：支援關鍵字搜尋，自動區分首次載入與 HTMX 局部更新"""
    query = request.GET.get('q', '').strip()
    books = get_books_queryset(query)

    context = {
        'books': books,
        'query': query,
    }

    if is_htmx(request):
        return render(request, 'books/partials/book_list.html', context)
    return render(request, 'books/base.html', context)


@login_required
@require_permission('circulation.borrow')
@require_http_methods(["POST"])
def borrow_copy_view(request, copy_id):
    """HTMX 借書端點：執行原子性借閱交易，並回傳更新後的清單與 Modal 通知"""
    copy = get_object_or_404(BookCopy, id=copy_id)
    try:
        with transaction.atomic():
            loan = _invoke_library_service(('borrow', 'checkout', 'loan'), request.user, copy)
            copy.refresh_from_db()
            if hasattr(copy, 'status') and copy.status == 'AVAILABLE':
                copy.status = 'BORROWED'
                copy.save(update_fields=['status'])

        due_str = f"，應還日期：{loan.due_date.strftime('%Y-%m-%d')}" if getattr(loan, 'due_date', None) else ""
        barcode_str = getattr(copy, 'barcode', copy.id)
        alert_type = 'success'
        alert_title = '借閱成功'
        alert_message = f'已成功借出《{copy.book.title}》（條碼：{barcode_str}）{due_str}。'
    except Exception as exc:
        alert_type = 'danger'
        alert_title = '借閱失敗'
        alert_message = str(exc)

    response = render(request, 'books/partials/book_list.html', {
        'books': get_books_queryset(),
        'alert_type': alert_type,
        'alert_title': alert_title,
        'alert_message': alert_message,
        'show_modal': True,
    })
    response['HX-Trigger'] = json.dumps({'showNotificationModal': True})
    return response


@login_required
@require_permission('circulation.return')
@require_http_methods(["POST"])
def return_copy_view(request, copy_id):
    """HTMX 還書端點：執行原子性歸還交易，並即時刷新館藏狀態"""
    copy = get_object_or_404(BookCopy, id=copy_id)
    try:
        with transaction.atomic():
            _invoke_library_service(('return', 'checkin'), request.user, copy)
            copy.refresh_from_db()
            if hasattr(copy, 'status') and copy.status != 'AVAILABLE':
                copy.status = 'AVAILABLE'
                copy.save(update_fields=['status'])

        barcode_str = getattr(copy, 'barcode', copy.id)
        alert_type = 'success'
        alert_title = '歸還完成'
        alert_message = f'書籍《{copy.book.title}》（條碼：{barcode_str}）已完成歸還入庫。'
    except Exception as exc:
        alert_type = 'danger'
        alert_title = '歸還異常'
        alert_message = str(exc)

    response = render(request, 'books/partials/book_list.html', {
        'books': get_books_queryset(),
        'alert_type': alert_type,
        'alert_title': alert_title,
        'alert_message': alert_message,
        'show_modal': True,
    })
    response['HX-Trigger'] = json.dumps({'showNotificationModal': True})
    return response


@login_required
@require_http_methods(["POST"])
def reserve_book_view(request, book_id):
    """HTMX 預約書籍端點：供全數複本皆已借出時建立預約排隊"""
    book = get_object_or_404(Book, id=book_id)
    try:
        with transaction.atomic():
            _invoke_library_service(('reserve', 'hold'), request.user, book)
        alert_type = 'success'
        alert_title = '預約成功'
        alert_message = f'已為您登記預約《{book.title}》，書籍歸還後將發送通知。'
    except Exception as exc:
        alert_type = 'danger'
        alert_title = '預約失敗'
        alert_message = str(exc)

    response = render(request, 'books/partials/book_list.html', {
        'books': get_books_queryset(),
        'alert_type': alert_type,
        'alert_title': alert_title,
        'alert_message': alert_message,
        'show_modal': True,
    })
    response['HX-Trigger'] = json.dumps({'showNotificationModal': True})
    return response