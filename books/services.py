import uuid
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.contrib.auth.models import User
from .models import (
    Book, BookCopy, LoanRecord, LoanItem, Reservation,
    Fine, Payment, Member, Notification, AuditLog
)


class LibraryBusinessError(Exception):
    """自定義業務邏輯例外，供視圖層統一捕捉並回饋前端 Modal"""
    pass


class LibraryService:
    MAX_ACTIVE_LOANS = 5
    LOAN_PERIOD_DAYS = 14
    DAILY_OVERDUE_FINE = Decimal("5.00")
    MAX_ACTIVE_RESERVATIONS = 3
    PICKUP_WINDOW_DAYS = 3

    @staticmethod
    @transaction.atomic
    def create_book_with_copies(title: str, isbn: str, author: str, publisher: str,
                                category: str, tags: str, copy_count: int = 1, location: str = "A區-1排"):
        """1. 圖書管理流程：建立書目並批次產生實體館藏副本與條碼（書籍與副本分離）"""
        if copy_count < 1:
            raise LibraryBusinessError("館藏副本數量至少需為 1 本。")

        book, _ = Book.objects.get_or_create(
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
        """2. 借閱流程：含資料庫交易、列級鎖定(select_for_update)防止重複借出、未繳罰單檢查"""
        if not user.is_active:
            raise LibraryBusinessError("此讀者帳號已停權，無法借閱書籍。")

        member = getattr(user, "member_account", None)
        if member and member.status != Member.Status.ACTIVE:
            raise LibraryBusinessError(f"讀者證狀態為「{member.get_status_display()}」，無法借閱書籍。")

        # 核心設計原則 D：使用 select_for_update 鎖定該筆館藏副本列，防止並發重複借出
        try:
            copy = BookCopy.objects.select_for_update().select_related("book").get(barcode=barcode)
        except BookCopy.DoesNotExist:
            raise LibraryBusinessError(f"找不到條碼為「{barcode}」的館藏副本。")

        # 檢查是否尚有未結清的罰單
        unpaid_fines = Fine.objects.filter(
            user=user, status__in=[Fine.Status.UNPAID, Fine.Status.PARTIAL]
        ).exists()
        if unpaid_fines:
            raise LibraryBusinessError("您尚有未結清的逾期罰款，請先繳納後再借閱。")

        has_overdue = LoanRecord.objects.filter(
            user=user, status=LoanRecord.Status.ACTIVE, due_date__lt=timezone.now()
        ).exists()
        if has_overdue:
            raise LibraryBusinessError("您尚有逾期未還書籍，請先歸還後再借閱。")

        quota = member.max_loan_quota if member else LibraryService.MAX_ACTIVE_LOANS
        active_loans_count = LoanRecord.objects.filter(user=user, status=LoanRecord.Status.ACTIVE).count()
        if active_loans_count >= quota:
            raise LibraryBusinessError(f"已達個人借閱上限（最多 {quota} 本）。")

        if copy.status == BookCopy.Status.RESERVED:
            ready_res = Reservation.objects.select_for_update().filter(
                book=copy.book,
                assigned_copy=copy,
                status=Reservation.Status.READY
            ).first()
            if not ready_res or ready_res.user != user:
                raise LibraryBusinessError("此副本已保留給其他預約順位讀者，無法直接借閱。")
            ready_res.status = Reservation.Status.COMPLETED
            ready_res.save()
        elif copy.status != BookCopy.Status.AVAILABLE:
            raise LibraryBusinessError(f"此館藏目前狀態為「{copy.get_status_display()}」，無法借閱。")

        now = timezone.now()
        due_date = now + timedelta(days=LibraryService.LOAN_PERIOD_DAYS)
        loan = LoanRecord.objects.create(
            user=user,
            member=member,
            copy=copy,
            borrowed_at=now,
            due_date=due_date,
            status=LoanRecord.Status.ACTIVE
        )
        LoanItem.objects.create(loan=loan, copy=copy, item_status="BORROWED")

        copy.status = BookCopy.Status.BORROWED
        copy.save()

        AuditLog.objects.create(
            user=user,
            action="BORROW_BOOK",
            target=copy.barcode,
            detail=f"借閱書目《{copy.book.title}》，應還日 {due_date:%Y-%m-%d}"
        )
        return loan

    @staticmethod
    @transaction.atomic
    def return_book(barcode: str, return_time=None) -> dict:
        """2. 歸還流程：計算逾期費用並開立獨立 Fine 罰單紀錄、更新館藏狀態並通知第一順位預約者"""
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

        fine_amount = Decimal("0.00")
        fine_record = None
        is_overdue = False
        if return_time > loan.due_date:
            is_overdue = True
            overdue_days = max(1, (return_time.date() - loan.due_date.date()).days)
            fine_amount = Decimal(overdue_days) * LibraryService.DAILY_OVERDUE_FINE
            fine_record = Fine.objects.create(
                loan=loan,
                user=loan.user,
                amount=fine_amount,
                reason=f"書籍《{copy.book.title}》({copy.barcode}) 逾期 {overdue_days} 天",
                status=Fine.Status.UNPAID
            )

        loan.returned_at = return_time
        loan.fine_amount = fine_amount
        loan.status = LoanRecord.Status.RETURNED
        loan.save()
        LoanItem.objects.filter(loan=loan, copy=copy).update(item_status="RETURNED")

        next_reservation = Reservation.objects.select_for_update().filter(
            book=copy.book,
            status=Reservation.Status.WAITING
        ).order_by("created_at").first()

        notified_user = None
        if next_reservation:
            copy.status = BookCopy.Status.RESERVED
            copy.save()

            deadline = return_time + timedelta(days=LibraryService.PICKUP_WINDOW_DAYS)
            next_reservation.status = Reservation.Status.READY
            next_reservation.assigned_copy = copy
            next_reservation.pickup_deadline = deadline
            next_reservation.save()
            notified_user = next_reservation.user.username

            Notification.objects.create(
                user=next_reservation.user,
                title="預約書籍到館通知",
                message=f"您預約的《{copy.book.title}》(條碼 {copy.barcode}) 已到館保留，請於 {deadline:%Y-%m-%d %H:%M} 前取書。"
            )
        else:
            copy.status = BookCopy.Status.AVAILABLE
            copy.save()

        return {
            "loan": loan,
            "is_overdue": is_overdue,
            "fine": fine_amount,
            "fine_record": fine_record,
            "notified_user": notified_user,
        }

    @staticmethod
    @transaction.atomic
    def record_fine_payment(fine_id: int, amount: Decimal,
                            transaction_type: str = Payment.TransactionType.PAYMENT,
                            operator: User = None) -> Payment:
        """財務金流交易：核心設計原則 C - 所有繳費、折抵、退款皆寫入獨立 Payment 流水紀錄"""
        if amount <= Decimal("0.00"):
            raise LibraryBusinessError("交易金額必須大於 0 元。")

        fine = Fine.objects.select_for_update().get(id=fine_id)
        tx_ref = f"TX-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"

        payment = Payment.objects.create(
            fine=fine,
            user=fine.user,
            transaction_type=transaction_type,
            amount=amount,
            reference_no=tx_ref,
            operator=operator
        )

        # 分別彙總實際繳費、折抵減免與退款金額
        cash_paid_total = fine.payments.filter(
            transaction_type=Payment.TransactionType.PAYMENT
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        waiver_total = fine.payments.filter(
            transaction_type=Payment.TransactionType.WAIVER
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        refund_total = fine.payments.filter(
            transaction_type=Payment.TransactionType.REFUND
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        net_paid = cash_paid_total + waiver_total - refund_total
        if net_paid >= fine.amount:
            # 若完全無實際現金繳納、全額皆由減免折抵覆蓋，才標記為 WAIVED；否則標記為 PAID
            if cash_paid_total == Decimal("0.00") and waiver_total >= fine.amount:
                fine.status = Fine.Status.WAIVED
            else:
                fine.status = Fine.Status.PAID
        elif net_paid > Decimal("0.00"):
            fine.status = Fine.Status.PARTIAL
        else:
            fine.status = Fine.Status.UNPAID
        fine.save()

        return payment
    
    @staticmethod
    @transaction.atomic
    def reserve_book(user: User, book_id: int) -> tuple[Reservation, int]:
        """3. 預約與候補流程"""
        try:
            book = Book.objects.get(id=book_id)
        except Book.DoesNotExist:
            raise LibraryBusinessError("指定的書目不存在。")

        if book.available_copies_count > 0:
            raise LibraryBusinessError("本書目尚有在館可借副本，請直接辦理借閱，無需預約。")

        already_reserved = Reservation.objects.filter(
            user=user,
            book=book,
            status__in=[Reservation.Status.WAITING, Reservation.Status.READY]
        ).exists()
        if already_reserved:
            raise LibraryBusinessError("您已在《" + book.title + "》的預約名單中，請勿重複預約。")

        already_borrowed = LoanRecord.objects.filter(
            user=user,
            copy__book=book,
            status=LoanRecord.Status.ACTIVE
        ).exists()
        if already_borrowed:
            raise LibraryBusinessError("您目前已借閱此書目，歸還後才可再次預約。")

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

        queue_position = Reservation.objects.filter(
            book=book,
            status=Reservation.Status.WAITING,
            created_at__lte=reservation.created_at
        ).count()

        return reservation, queue_position

    @staticmethod
    @transaction.atomic
    def process_expired_reservations(current_time=None) -> int:
        """3. 預約逾期處理"""
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
        """3. 館藏損壞異常處理"""
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
                    affected_res.status = Reservation.Status.WAITING
                    affected_res.assigned_copy = None
                    affected_res.pickup_deadline = None
                    affected_res.save()

        valid_copies_exist = BookCopy.objects.filter(book=copy.book).exclude(
            status__in=[BookCopy.Status.DAMAGED, BookCopy.Status.RETIRED]
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
