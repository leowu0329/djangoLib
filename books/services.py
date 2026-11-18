import uuid
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.models import User
from .models import Book, BookCopy, LoanRecord, Reservation


class LibraryBusinessError(Exception):
    """自定義業務邏輯例外，供視圖層統一捕捉並回饋前端 Modal"""
    pass


class LibraryService:
    # 系統全域業務規則常數
    MAX_ACTIVE_LOANS = 5           # 讀者最多同時借閱 5 本
    LOAN_PERIOD_DAYS = 14          # 單次借閱天數 14 天
    DAILY_OVERDUE_FINE = Decimal("5.00")  # 每日逾期罰金 5 元
    MAX_ACTIVE_RESERVATIONS = 3    # 讀者最多同時有效預約 3 本
    PICKUP_WINDOW_DAYS = 3         # 到館通知後取書有效期限 3 天

    @staticmethod
    @transaction.atomic
    def create_book_with_copies(title: str, isbn: str, author: str, publisher: str,
                                category: str, tags: str, copy_count: int = 1, location: str = "A區-1排"):
        """1. 圖書管理流程：建立書目並批次產生實體館藏副本與條碼"""
        if copy_count < 1:
            raise LibraryBusinessError("館藏副本數量至少需為 1 本。")

        book, created = Book.objects.get_or_create(
            isbn=isbn,
            defaults={
                "title": title,
                "author": author,
                "publisher": publisher,
                "category": category,
                "tags": tags,
            }
        )

        created_copies = []
        existing_count = book.copies.count()
        for idx in range(1, copy_count + 1):
            # 產生唯一館藏條碼：ISBN末4碼 + 流水號 + 短UUID
            short_uid = uuid.uuid4().hex[:4].upper()
            barcode = f"LIB-{isbn[-4:]}-{existing_count + idx:03d}-{short_uid}"
            copy = BookCopy.objects.create(
                book=book,
                barcode=barcode,
                location=location,
                status=BookCopy.Status.AVAILABLE
            )
            created_copies.append(copy)

        return book, created_copies

    @staticmethod
    @transaction.atomic
    def borrow_book(user: User, barcode: str) -> LoanRecord:
        """2. 借閱流程：含資格檢查、預約優先權核對、數量上限與應還日期計算"""
        if not user.is_active:
            raise LibraryBusinessError("此讀者帳號已停權，無法借閱書籍。")

        try:
            copy = BookCopy.objects.select_for_update().select_related("book").get(barcode=barcode)
        except BookCopy.DoesNotExist:
            raise LibraryBusinessError(f"找不到條碼為「{barcode}」的館藏副本。")

        # 檢查讀者是否有逾期未還書籍或未繳罰金
        has_overdue = LoanRecord.objects.filter(
            user=user, status=LoanRecord.Status.ACTIVE, due_date__lt=timezone.now()
        ).exists()
        if has_overdue:
            raise LibraryBusinessError("您尚有逾期未還書籍，請先歸還後再借閱。")

        # 檢查借閱上限
        active_loans_count = LoanRecord.objects.filter(user=user, status=LoanRecord.Status.ACTIVE).count()
        if active_loans_count >= LibraryService.MAX_ACTIVE_LOANS:
            raise LibraryBusinessError(f"已達個人借閱上限（最多 {LibraryService.MAX_ACTIVE_LOANS} 本）。")

        # 檢查館藏狀態與預約保留歸屬
        if copy.status == BookCopy.Status.RESERVED:
            ready_res = Reservation.objects.filter(
                book=copy.book,
                assigned_copy=copy,
                status=Reservation.Status.READY
            ).first()
            if not ready_res or ready_res.user != user:
                raise LibraryBusinessError("此副本已保留給其他預約順位讀者，無法直接借閱。")
            # 預約者本人前來取書，將預約單轉為已完成
            ready_res.status = Reservation.Status.COMPLETED
            ready_res.save()
        elif copy.status != BookCopy.Status.AVAILABLE:
            raise LibraryBusinessError(f"此館藏目前狀態為「{copy.get_status_display()}」，無法借閱。")

        # 建立借閱紀錄並更新副本狀態
        now = timezone.now()
        due_date = now + timedelta(days=LibraryService.LOAN_PERIOD_DAYS)
        loan = LoanRecord.objects.create(
            user=user,
            copy=copy,
            borrowed_at=now,
            due_date=due_date,
            status=LoanRecord.Status.ACTIVE
        )

        copy.status = BookCopy.Status.BORROWED
        copy.save()
        return loan

    @staticmethod
    @transaction.atomic
    def return_book(barcode: str, return_time=None) -> dict:
        """2. 歸還流程：計算逾期費用、更新館藏狀態，並自動觸發預約隊列通知"""
        return_time = return_time or timezone.now()
        try:
            copy = BookCopy.objects.select_for_update().select_related("book").get(barcode=barcode)
        except BookCopy.DoesNotExist:
            raise LibraryBusinessError(f"找不到條碼為「{barcode}」的館藏副本。")

        loan = LoanRecord.objects.select_for_update().filter(
            copy=copy, status=LoanRecord.Status.ACTIVE
        ).first()
        if not loan:
            raise LibraryBusinessError(f"條碼「{barcode}」目前無進行中的借閱紀錄。")

        # 判斷是否逾期並計算罰金
        fine = Decimal("0.00")
        is_overdue = False
        if return_time > loan.due_date:
            is_overdue = True
            overdue_days = (return_time.date() - loan.due_date.date()).days
            overdue_days = max(1, overdue_days)
            fine = Decimal(overdue_days) * LibraryService.DAILY_OVERDUE_FINE

        loan.returned_at = return_time
        loan.fine_amount = fine
        loan.status = LoanRecord.Status.RETURNED
        loan.save()

        # 檢查是否有排隊中的預約者 (FIFO 第一順位)
        next_reservation = Reservation.objects.select_for_update().filter(
            book=copy.book,
            status=Reservation.Status.WAITING
        ).order_by("created_at").first()

        notified_user = None
        if next_reservation:
            copy.status = BookCopy.Status.RESERVED
            copy.save()

            next_reservation.status = Reservation.Status.READY
            next_reservation.assigned_copy = copy
            next_reservation.pickup_deadline = return_time + timedelta(days=LibraryService.PICKUP_WINDOW_DAYS)
            next_reservation.save()
            notified_user = next_reservation.user.username
        else:
            copy.status = BookCopy.Status.AVAILABLE
            copy.save()

        return {
            "loan": loan,
            "is_overdue": is_overdue,
            "fine": fine,
            "notified_user": notified_user,
        }

    @staticmethod
    @transaction.atomic
    def reserve_book(user: User, book_id: int) -> tuple[Reservation, int]:
        """3. 預約與候補流程：驗證重複預約、預約上限與可預約條件"""
        try:
            book = Book.objects.get(id=book_id)
        except Book.DoesNotExist:
            raise LibraryBusinessError("指定的書目不存在。")

        # 規則 A：僅當所有有效館藏皆已借出/保留時才需預約（若有在館書應直接借閱）
        if book.available_copies_count > 0:
            raise LibraryBusinessError("本書目尚有在館可借副本，請直接辦理借閱，無需預約。")

        # 規則 B：不允許同一讀者對同一本書重複建立有效預約
        already_reserved = Reservation.objects.filter(
            user=user,
            book=book,
            status__in=[Reservation.Status.WAITING, Reservation.Status.READY]
        ).exists()
        if already_reserved:
            raise LibraryBusinessError("您已在《" + book.title + "》的預約名單中，請勿重複預約。")

        # 規則 C：檢查讀者是否已借閱同一本書且尚未歸還
        already_borrowed = LoanRecord.objects.filter(
            user=user,
            copy__book=book,
            status=LoanRecord.Status.ACTIVE
        ).exists()
        if already_borrowed:
            raise LibraryBusinessError("您目前已借閱此書目，歸還後才可再次預約。")

        # 規則 D：每位讀者有效預約上限檢查
        active_res_count = Reservation.objects.filter(
            user=user,
            status__in=[Reservation.Status.WAITING, Reservation.Status.READY]
        ).count()
        if active_res_count >= LibraryService.MAX_ACTIVE_RESERVATIONS:
            raise LibraryBusinessError(f"已達個人預約上限（最多 {LibraryService.MAX_ACTIVE_RESERVATIONS} 本）。")

        reservation = Reservation.objects.create(
            user=user,
            book=book,
            status=Reservation.Status.WAITING
        )

        # 計算目前候補順位
        queue_position = Reservation.objects.filter(
            book=book,
            status=Reservation.Status.WAITING,
            created_at__lte=reservation.created_at
        ).count()

        return reservation, queue_position

    @staticmethod
    @transaction.atomic
    def process_expired_reservations(current_time=None) -> int:
        """3. 預約逾期處理：取消逾時未取書的預約，並將該副本遞補通知下一順位"""
        current_time = current_time or timezone.now()
        expired_list = Reservation.objects.select_for_update().filter(
            status=Reservation.Status.READY,
            pickup_deadline__lt=current_time
        ).select_related("book", "assigned_copy")

        processed_count = 0
        for res in expired_list:
            copy = res.assigned_copy
            res.status = Reservation.Status.CANCELLED
            res.assigned_copy = None
            res.save()
            processed_count += 1

            if copy and copy.status == BookCopy.Status.RESERVED:
                next_waiting = Reservation.objects.select_for_update().filter(
                    book=res.book,
                    status=Reservation.Status.WAITING
                ).order_by("created_at").first()

                if next_waiting:
                    next_waiting.status = Reservation.Status.READY
                    next_waiting.assigned_copy = copy
                    next_waiting.pickup_deadline = current_time + timedelta(days=LibraryService.PICKUP_WINDOW_DAYS)
                    next_waiting.save()
                else:
                    copy.status = BookCopy.Status.AVAILABLE
                    copy.save()

        return processed_count

    @staticmethod
    @transaction.atomic
    def mark_copy_damaged(barcode: str) -> dict:
        """3. 館藏損壞異常處理：標記損壞並轉移受影響的保留預約（或若全館無可用副本則取消候補）"""
        try:
            copy = BookCopy.objects.select_for_update().select_related("book").get(barcode=barcode)
        except BookCopy.DoesNotExist:
            raise LibraryBusinessError(f"找不到條碼為「{barcode}」的館藏副本。")

        if copy.status == BookCopy.Status.BORROWED:
            raise LibraryBusinessError("書籍目前為「借出」狀態，請先執行歸還程序後再標記損壞。")

        old_status = copy.status
        copy.status = BookCopy.Status.DAMAGED
        copy.save()

        reassigned_to = None
        cancelled_all_waiting = False

        # 情況 1：若此副本原本處於「預約保留中 (READY)」，需嘗試尋找其他在館副本遞補，或退回候補第一順位
        if old_status == BookCopy.Status.RESERVED:
            affected_res = Reservation.objects.select_for_update().filter(
                assigned_copy=copy, status=Reservation.Status.READY
            ).first()
            if affected_res:
                replacement_copy = BookCopy.objects.select_for_update().filter(
                    book=copy.book, status=BookCopy.Status.AVAILABLE
                ).first()
                if replacement_copy:
                    replacement_copy.status = BookCopy.Status.RESERVED
                    replacement_copy.save()
                    affected_res.assigned_copy = replacement_copy
                    affected_res.save()
                    reassigned_to = replacement_copy.barcode
                else:
                    # 無其他在館副本，將該讀者退回 WAITING 最優先狀態等待下一本歸還
                    affected_res.status = Reservation.Status.WAITING
                    affected_res.assigned_copy = None
                    affected_res.pickup_deadline = None
                    affected_res.save()

        # 情況 2：若此書目所有副本皆已損壞（無任何非損壞副本），自動取消所有等待中的預約並釋放名額
        valid_copies_exist = BookCopy.objects.filter(book=copy.book).exclude(
            status=BookCopy.Status.DAMAGED
        ).exists()
        if not valid_copies_exist:
            Reservation.objects.filter(
                book=copy.book,
                status__in=[Reservation.Status.WAITING, Reservation.Status.READY]
            ).update(status=Reservation.Status.CANCELLED, assigned_copy=None)
            cancelled_all_waiting = True

        return {
            "barcode": barcode,
            "reassigned_to": reassigned_to,
            "cancelled_all_waiting": cancelled_all_waiting
        }
