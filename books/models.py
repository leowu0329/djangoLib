from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


# ==========================================
# 第一部分：RBAC 角色權限與系統稽核紀錄模型
# ==========================================

class Role(models.Model):
    """系統角色定義表 (roles)"""
    code = models.CharField("角色代碼", max_length=50, unique=True, default="READER")
    name = models.CharField("角色名稱", max_length=50)
    description = models.CharField("角色說明", max_length=255, blank=True)
    permissions = models.JSONField("權限清單(相容舊版)", default=list, blank=True)

    class Meta:
        db_table = "roles"

    def __str__(self):
        return f"{self.name} ({self.code})"


class Permission(models.Model):
    """系統權限項目表 (permissions)"""
    code = models.CharField("權限代碼", max_length=100, unique=True)
    name = models.CharField("權限名稱", max_length=100)
    description = models.CharField("權限說明", max_length=255, blank=True)

    class Meta:
        db_table = "permissions"

    def __str__(self):
        return f"{self.code} - {self.name}"


class RolePermission(models.Model):
    """角色權限關聯對照表 (role_permissions)"""
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="permission_roles")
    granted_at = models.DateTimeField("授權時間", auto_now_add=True)

    class Meta:
        db_table = "role_permissions"
        unique_together = ("role", "permission")


class UserProfile(models.Model):
    """使用者個人資料與角色綁定表 (profiles)"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile", verbose_name="使用者")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="profiles", verbose_name="所屬角色")
    phone = models.CharField("聯絡電話", max_length=30, blank=True)
    address = models.CharField("聯絡地址", max_length=255, blank=True)
    card_number = models.CharField("讀者證號", max_length=50, blank=True, null=True, unique=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)

    class Meta:
        db_table = "profiles"

    def __str__(self):
        role_name = self.role.name if self.role else "無角色"
        return f"{self.user.username} ({role_name})"


class Member(models.Model):
    """讀者資料表 (members) - 獨立管理借閱資格與停權狀態"""
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "正常"
        SUSPENDED = "SUSPENDED", "停權"
        EXPIRED = "EXPIRED", "證件過期"

    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="member_account", verbose_name="關聯帳號")
    member_code = models.CharField("讀者編號", max_length=50, unique=True)
    full_name = models.CharField("讀者姓名", max_length=100)
    status = models.CharField("讀者狀態", max_length=20, choices=Status.choices, default=Status.ACTIVE)
    max_loan_quota = models.PositiveIntegerField("最大可借冊數", default=5)
    joined_at = models.DateTimeField("辦證日期", auto_now_add=True)

    class Meta:
        db_table = "members"

    def __str__(self):
        return f"[{self.member_code}] {self.full_name} ({self.get_status_display()})"


class UserRole(models.Model):
    """使用者與多角色關聯表 (保留相容性)"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles", verbose_name="使用者")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_users", verbose_name="角色")
    assigned_at = models.DateTimeField("指派時間", auto_now_add=True)

    class Meta:
        unique_together = ("user", "role")


class AuditLog(models.Model):
    """系統操作稽核日誌 (audit_logs)"""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="操作者")
    action = models.CharField("操作動作", max_length=100)
    target = models.CharField("操作目標", max_length=255, blank=True)
    detail = models.TextField("詳細內容", blank=True)
    ip_address = models.GenericIPAddressField("來源IP", null=True, blank=True)
    created_at = models.DateTimeField("操作時間", auto_now_add=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]


class SystemSetting(models.Model):
    """系統參數設定表 (system_settings)"""
    key = models.CharField("設定鍵值", max_length=100, unique=True)
    value = models.CharField("設定內容", max_length=255)
    description = models.CharField("說明", max_length=255, blank=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        db_table = "system_settings"


# ==========================================
# 第二部分：書目正規化與分館書架模型 (原則 A)
# ==========================================

class Publisher(models.Model):
    """出版社資料表 (publishers)"""
    name = models.CharField("出版社名稱", max_length=150, unique=True)
    country = models.CharField("國家/地區", max_length=50, blank=True)

    class Meta:
        db_table = "publishers"

    def __str__(self):
        return self.name


class Category(models.Model):
    """圖書分類資料表 (categories)"""
    code = models.CharField("分類號", max_length=50, unique=True)
    name = models.CharField("分類名稱", max_length=100)

    class Meta:
        db_table = "categories"

    def __str__(self):
        return f"{self.code} {self.name}"


class Author(models.Model):
    """作者資料表 (authors)"""
    name = models.CharField("作者姓名", max_length=150)
    biography = models.TextField("作者簡介", blank=True)

    class Meta:
        db_table = "authors"

    def __str__(self):
        return self.name


class Branch(models.Model):
    """分館資料表 (branches)"""
    code = models.CharField("分館代碼", max_length=30, unique=True)
    name = models.CharField("分館名稱", max_length=100)
    address = models.CharField("分館地址", max_length=255, blank=True)

    class Meta:
        db_table = "branches"

    def __str__(self):
        return self.name


class Shelf(models.Model):
    """書架位置資料表 (shelves)"""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="shelves", verbose_name="所屬分館")
    code = models.CharField("書架編號", max_length=50)
    floor = models.CharField("樓層區域", max_length=50, blank=True)

    class Meta:
        db_table = "shelves"
        unique_together = ("branch", "code")

    def __str__(self):
        return f"{self.branch.name} - {self.code}"


class Book(models.Model):
    """書目資料表 (books)：儲存書籍共通資訊（與實體副本分離）"""
    title = models.CharField("書名", max_length=255)
    isbn = models.CharField("ISBN", max_length=20, unique=True)
    author = models.CharField("主要作者顯示", max_length=150)
    publisher = models.CharField("出版社名稱顯示", max_length=150)
    publisher_ref = models.ForeignKey(Publisher, on_delete=models.SET_NULL, null=True, blank=True, related_name="books")
    category = models.CharField("分類顯示", max_length=100, default="一般類")
    category_ref = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="books")
    authors = models.ManyToManyField(Author, through="BookAuthor", related_name="books")
    tags = models.CharField("標籤", max_length=255, blank=True, help_text="以逗號分隔")
    created_at = models.DateTimeField("建立時間", auto_now_add=True)

    class Meta:
        db_table = "books"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.isbn})"

    @property
    def available_copies_count(self):
        return self.copies.filter(status=BookCopy.Status.AVAILABLE).count()

    @property
    def total_copies_count(self):
        return self.copies.exclude(status=BookCopy.Status.RETIRED).count()


class BookAuthor(models.Model):
    """書籍與作者多對多關聯表 (book_authors)"""
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    author = models.ForeignKey(Author, on_delete=models.CASCADE)
    author_order = models.PositiveSmallIntegerField("作者排序", default=1)

    class Meta:
        db_table = "book_authors"
        unique_together = ("book", "author")


class BookCopy(models.Model):
    """館藏副本資料表 (book_copies)：每本實體書擁有獨立條碼、分館、書架與狀態"""
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "在館可借"
        BORROWED = "BORROWED", "已借出"
        RESERVED = "RESERVED", "預約保留中"
        DAMAGED = "DAMAGED", "損壞/遺失維護中"
        RETIRED = "RETIRED", "已報廢下架"

    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="copies", verbose_name="所屬書目")
    barcode = models.CharField("館藏條碼", max_length=50, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="copies", verbose_name="所屬分館")
    shelf = models.ForeignKey(Shelf, on_delete=models.SET_NULL, null=True, blank=True, related_name="copies", verbose_name="書架位置")
    location = models.CharField("館藏位置顯示", max_length=100, default="A區-一般書架")
    status = models.CharField("館藏狀態", max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    created_at = models.DateTimeField("入庫時間", auto_now_add=True)

    class Meta:
        db_table = "book_copies"

    def __str__(self):
        return f"[{self.barcode}] {self.book.title} - {self.get_status_display()}"


# ==========================================
# 第三部分：借閱、預約、財務交易與採購盤點模型 (原則 B & C)
# ==========================================

class LoanRecord(models.Model):
    """借閱紀錄資料表 (loans)：原則 B - 不可直接刪除，使用 PROTECT 保護歷史關聯"""
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "借閱中"
        RETURNED = "RETURNED", "已歸還"
        OVERDUE = "OVERDUE", "逾期未還"

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="loans", verbose_name="借閱讀者")
    member = models.ForeignKey(Member, on_delete=models.PROTECT, null=True, blank=True, related_name="loans", verbose_name="讀者檔案")
    copy = models.ForeignKey(BookCopy, on_delete=models.PROTECT, related_name="loans", verbose_name="館藏副本")
    borrowed_at = models.DateTimeField("借出日期", default=timezone.now)
    due_date = models.DateTimeField("應還日期")
    returned_at = models.DateTimeField("實際歸還日期", null=True, blank=True)
    fine_amount = models.DecimalField("逾期罰金彙總(元)", max_digits=8, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField("借閱狀態", max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "loans"

    def delete(self, *args, **kwargs):
        raise ValueError("資料庫安全規範：借閱歷史紀錄 (loans) 禁止直接刪除，請保留完整稽核軌跡。")

    def __str__(self):
        return f"{self.user.username} 借閱 {self.copy.barcode}"


class LoanItem(models.Model):
    """借閱明細資料表 (loan_items)：支援單次借閱批次明細"""
    loan = models.ForeignKey(LoanRecord, on_delete=models.PROTECT, related_name="items")
    copy = models.ForeignKey(BookCopy, on_delete=models.PROTECT, related_name="loan_items")
    item_status = models.CharField("明細狀態", max_length=20, default="BORROWED")

    class Meta:
        db_table = "loan_items"


class Reservation(models.Model):
    """預約與候補隊列資料表 (reservations)"""
    class Status(models.TextChoices):
        WAITING = "WAITING", "排隊候補中"
        READY = "READY", "待取書(已保留)"
        COMPLETED = "COMPLETED", "已完成取書"
        CANCELLED = "CANCELLED", "已取消/逾期失效"

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reservations", verbose_name="預約讀者")
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="reservations", verbose_name="預約書目")
    assigned_copy = models.ForeignKey(
        BookCopy, on_delete=models.SET_NULL, null=True, blank=True, related_name="reserved_for", verbose_name="保留副本"
    )
    status = models.CharField("預約狀態", max_length=20, choices=Status.choices, default=Status.WAITING)
    created_at = models.DateTimeField("預約登記時間", auto_now_add=True)
    pickup_deadline = models.DateTimeField("取書截止期限", null=True, blank=True)

    class Meta:
        db_table = "reservations"
        ordering = ["created_at"]


class Fine(models.Model):
    """逾期罰款紀錄表 (fines)：原則 C - 所有金額皆有獨立憑證紀錄"""
    class Status(models.TextChoices):
        UNPAID = "UNPAID", "未繳納"
        PARTIAL = "PARTIAL", "部分繳納"
        PAID = "PAID", "已結清"
        WAIVED = "WAIVED", "已折抵/減免"

    loan = models.ForeignKey(LoanRecord, on_delete=models.PROTECT, related_name="fines", verbose_name="關聯借閱紀錄")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="fines", verbose_name="應繳讀者")
    amount = models.DecimalField("罰款金額", max_digits=8, decimal_places=2)
    reason = models.CharField("罰款原因", max_length=255, default="逾期歸還罰金")
    status = models.CharField("繳費狀態", max_length=20, choices=Status.choices, default=Status.UNPAID)
    created_at = models.DateTimeField("開立時間", auto_now_add=True)

    class Meta:
        db_table = "fines"


class Payment(models.Model):
    """繳費/折抵/退款交易流水表 (payments)：原則 C - 每筆金流獨立入帳"""
    class TransactionType(models.TextChoices):
        PAYMENT = "PAYMENT", "現金/線上繳費"
        WAIVER = "WAIVER", "罰金折抵減免"
        REFUND = "REFUND", "溢繳退款"

    fine = models.ForeignKey(Fine, on_delete=models.PROTECT, related_name="payments", verbose_name="關聯罰單")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="payments", verbose_name="交易讀者")
    transaction_type = models.CharField("交易類型", max_length=20, choices=TransactionType.choices, default=TransactionType.PAYMENT)
    amount = models.DecimalField("交易金額", max_digits=8, decimal_places=2)
    reference_no = models.CharField("交易序號", max_length=64, unique=True)
    operator = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="processed_payments", verbose_name="經手館員")
    created_at = models.DateTimeField("交易時間", auto_now_add=True)

    class Meta:
        db_table = "payments"


class Acquisition(models.Model):
    """採購資料表 (acquisitions)"""
    isbn = models.CharField("採購ISBN", max_length=20)
    title = models.CharField("採購書名", max_length=255)
    quantity = models.PositiveIntegerField("採購數量", default=1)
    unit_price = models.DecimalField("單價", max_digits=8, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField("採購狀態", max_length=20, default="ORDERED")
    created_at = models.DateTimeField("建立時間", auto_now_add=True)

    class Meta:
        db_table = "acquisitions"


class InventoryLog(models.Model):
    """館藏盤點紀錄表 (inventory_logs)"""
    copy = models.ForeignKey(BookCopy, on_delete=models.PROTECT, related_name="inventory_logs")
    checked_by = models.ForeignKey(User, on_delete=models.PROTECT)
    observed_status = models.CharField("盤點狀態", max_length=20)
    note = models.CharField("盤點備註", max_length=255, blank=True)
    checked_at = models.DateTimeField("盤點時間", auto_now_add=True)

    class Meta:
        db_table = "inventory_logs"


class Notification(models.Model):
    """系統通知紀錄表 (notifications)"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField("通知管道", max_length=20, default="SYSTEM")
    title = models.CharField("通知標題", max_length=150)
    message = models.TextField("通知內容")
    is_read = models.BooleanField("已讀狀態", default=False)
    created_at = models.DateTimeField("發送時間", auto_now_add=True)

    class Meta:
        db_table = "notifications"
