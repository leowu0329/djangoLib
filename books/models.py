from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


# ==========================================
# 第一部分：RBAC 角色權限與系統稽核紀錄模型
# ==========================================

class Role(models.Model):
    """系統角色定義表 (相容 init_rbac.py 的 code / name / permissions 結構)"""
    code = models.CharField("角色代碼", max_length=50, unique=True, default="READER")
    name = models.CharField("角色名稱", max_length=50)
    description = models.CharField("角色說明", max_length=255, blank=True)
    permissions = models.JSONField("權限清單", default=list, blank=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class UserProfile(models.Model):
    """使用者擴充檔案與角色綁定表"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile", verbose_name="使用者")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="profiles", verbose_name="所屬角色")
    card_number = models.CharField("讀者證號", max_length=50, blank=True, null=True, unique=True)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)

    def __str__(self):
        role_name = self.role.name if self.role else "無角色"
        return f"{self.user.username} ({role_name})"


class UserRole(models.Model):
    """使用者與多角色關聯表 (保留相容性)"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles", verbose_name="使用者")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_users", verbose_name="角色")
    assigned_at = models.DateTimeField("指派時間", auto_now_add=True)

    class Meta:
        unique_together = ("user", "role")

    def __str__(self):
        return f"{self.user.username} - {self.role.name}"


class AuditLog(models.Model):
    """系統操作稽核日誌"""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="操作者")
    action = models.CharField("操作動作", max_length=100)
    target = models.CharField("操作目標", max_length=255, blank=True)
    detail = models.TextField("詳細內容", blank=True)
    ip_address = models.GenericIPAddressField("來源IP", null=True, blank=True)
    created_at = models.DateTimeField("操作時間", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.user} - {self.action}"


# ==========================================
# 第二部分：圖書書目、館藏副本與借還預約核心模型
# ==========================================

class Book(models.Model):
    """書目資料表：儲存書籍共通資訊"""
    title = models.CharField("書名", max_length=255)
    isbn = models.CharField("ISBN", max_length=20, unique=True)
    author = models.CharField("作者", max_length=150)
    publisher = models.CharField("出版社", max_length=150)
    category = models.CharField("分類", max_length=100, default="一般類")
    tags = models.CharField("標籤", max_length=255, blank=True, help_text="以逗號分隔")
    created_at = models.DateTimeField("建立時間", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.isbn})"

    @property
    def available_copies_count(self):
        return self.copies.filter(status=BookCopy.Status.AVAILABLE).count()

    @property
    def total_copies_count(self):
        return self.copies.count()


class BookCopy(models.Model):
    """館藏副本資料表：每本實體書擁有獨立條碼與狀態"""
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "在館可借"
        BORROWED = "BORROWED", "已借出"
        RESERVED = "RESERVED", "預約保留中"
        DAMAGED = "DAMAGED", "損壞/遺失維護中"

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="copies", verbose_name="所屬書目")
    barcode = models.CharField("館藏條碼", max_length=50, unique=True)
    location = models.CharField("館藏位置", max_length=100, default="A區-一般書架")
    status = models.CharField("館藏狀態", max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    created_at = models.DateTimeField("入庫時間", auto_now_add=True)

    def __str__(self):
        return f"[{self.barcode}] {self.book.title} - {self.get_status_display()}"


class LoanRecord(models.Model):
    """借閱紀錄資料表"""
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "借閱中"
        RETURNED = "RETURNED", "已歸還"
        OVERDUE = "OVERDUE", "逾期未還"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="loans", verbose_name="借閱讀者")
    copy = models.ForeignKey(BookCopy, on_delete=models.CASCADE, related_name="loans", verbose_name="館藏副本")
    borrowed_at = models.DateTimeField("借出日期", default=timezone.now)
    due_date = models.DateTimeField("應還日期")
    returned_at = models.DateTimeField("實際歸還日期", null=True, blank=True)
    fine_amount = models.DecimalField("逾期罰金(元)", max_digits=8, decimal_places=2, default=0.00)
    status = models.CharField("借閱狀態", max_length=20, choices=Status.choices, default=Status.ACTIVE)

    def __str__(self):
        return f"{self.user.username} 借閱 {self.copy.barcode}"


class Reservation(models.Model):
    """預約與候補隊列資料表"""
    class Status(models.TextChoices):
        WAITING = "WAITING", "排隊候補中"
        READY = "READY", "待取書(已保留)"
        COMPLETED = "COMPLETED", "已完成取書"
        CANCELLED = "CANCELLED", "已取消/逾期失效"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reservations", verbose_name="預約讀者")
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="reservations", verbose_name="預約書目")
    assigned_copy = models.ForeignKey(
        BookCopy, on_delete=models.SET_NULL, null=True, blank=True, related_name="reserved_for", verbose_name="保留副本"
    )
    status = models.CharField("預約狀態", max_length=20, choices=Status.choices, default=Status.WAITING)
    created_at = models.DateTimeField("預約登記時間", auto_now_add=True)
    pickup_deadline = models.DateTimeField("取書截止期限", null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.username} 預約《{self.book.title}》({self.get_status_display()})"