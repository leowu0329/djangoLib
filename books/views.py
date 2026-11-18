import json
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import HttpResponse
from .models import Book, BookCopy, LoanRecord, Reservation
from .services import LibraryService, LibraryBusinessError
from .permissions import librarian_required


def _build_dashboard_context(user):
    """組裝首頁與局部刷新所需的完整資料上下文"""
    books = Book.objects.prefetch_related("copies").all()
    active_loans = LoanRecord.objects.filter(user=user, status=LoanRecord.Status.ACTIVE).select_related("copy__book")
    user_reservations = Reservation.objects.filter(
        user=user, status__in=[Reservation.Status.WAITING, Reservation.Status.READY]
    ).select_related("book", "assigned_copy")
    is_librarian = user.groups.filter(name="Librarian").exists() or user.is_superuser

    return {
        "books": books,
        "active_loans": active_loans,
        "user_reservations": user_reservations,
        "is_librarian": is_librarian,
    }


def _htmx_response_with_modal(request, title: str, message: str, level: str = "success"):
    """渲染最新書目列表並透過 HX-Trigger 觸發前端 Bootstrap Modal"""
    context = _build_dashboard_context(request.user)
    response = render(request, "books/partials/book_list.html", context)
    trigger_payload = {
        "showModalAlert": {
            "title": title,
            "message": message,
            "level": level  # success / danger / warning
        }
    }
    response["HX-Trigger"] = json.dumps(trigger_payload)
    return response


@login_required
def index(request):
    context = _build_dashboard_context(request.user)
    return render(request, "books/base.html", context)


@login_required
@librarian_required
@require_POST
def add_book(request):
    """館員新增書目與館藏副本"""
    try:
        title = request.POST.get("title", "").strip()
        isbn = request.POST.get("isbn", "").strip()
        author = request.POST.get("author", "").strip()
        publisher = request.POST.get("publisher", "").strip()
        category = request.POST.get("category", "一般類").strip()
        tags = request.POST.get("tags", "").strip()
        copy_count = int(request.POST.get("copy_count", 1))
        location = request.POST.get("location", "A區-1排").strip()

        if not all([title, isbn, author, publisher]):
            raise LibraryBusinessError("書名、ISBN、作者與出版社為必填欄位。")

        book, copies = LibraryService.create_book_with_copies(
            title=title, isbn=isbn, author=author, publisher=publisher,
            category=category, tags=tags, copy_count=copy_count, location=location
        )
        barcodes = ", ".join([c.barcode for c in copies])
        return _htmx_response_with_modal(
            request, "上架成功", f"已建立《{book.title}》共 {len(copies)} 本副本。條碼：{barcodes}", "success"
        )
    except (LibraryBusinessError, ValueError) as e:
        return _htmx_response_with_modal(request, "新增失敗", str(e), "danger")


@login_required
@require_POST
def borrow_copy(request):
    """掃描條碼借閱書籍"""
    barcode = request.POST.get("barcode", "").strip()
    try:
        loan = LibraryService.borrow_book(request.user, barcode)
        due_str = loan.due_date.strftime("%Y-%m-%d")
        return _htmx_response_with_modal(
            request, "借閱成功", f"條碼 {barcode} 借閱完成！應還日期：{due_str}", "success"
        )
    except LibraryBusinessError as e:
        return _htmx_response_with_modal(request, "借閱受阻", str(e), "danger")


@login_required
@require_POST
def return_copy(request):
    """掃描條碼歸還書籍"""
    barcode = request.POST.get("barcode", "").strip()
    try:
        result = LibraryService.return_book(barcode)
        msg = f"條碼 {barcode} 已完成歸還。"
        if result["is_overdue"]:
            msg += f"【注意】已逾期，產生罰金 NT$ {result['fine']} 元。"
        if result["notified_user"]:
            msg += f" 本書已自動保留並通知第一順位預約者：{result['notified_user']}。"
        level = "warning" if result["is_overdue"] else "success"
        return _htmx_response_with_modal(request, "歸還完成", msg, level)
    except LibraryBusinessError as e:
        return _htmx_response_with_modal(request, "歸還失敗", str(e), "danger")


@login_required
@require_POST
def reserve_book(request, book_id):
    """讀者預約書目"""
    try:
        reservation, position = LibraryService.reserve_book(request.user, book_id)
        return _htmx_response_with_modal(
            request, "預約成功", f"已成功登記預約《{reservation.book.title}》，目前候補順位：第 {position} 位。", "success"
        )
    except LibraryBusinessError as e:
        return _htmx_response_with_modal(request, "無法預約", str(e), "danger")


@login_required
@librarian_required
@require_POST
def report_damaged(request):
    """館員回報館藏損壞"""
    barcode = request.POST.get("barcode", "").strip()
    try:
        res = LibraryService.mark_copy_damaged(barcode)
        msg = f"條碼 {barcode} 已標記為損壞維護中。"
        if res["reassigned_to"]:
            msg += f" 原保留預約已自動轉移至備用副本：{res['reassigned_to']}。"
        elif res["cancelled_all_waiting"]:
            msg += " 因本書目已無可用館藏，系統已自動取消所有候補預約。"
        return _htmx_response_with_modal(request, "館藏狀態更新", msg, "warning")
    except LibraryBusinessError as e:
        return _htmx_response_with_modal(request, "操作失敗", str(e), "danger")
