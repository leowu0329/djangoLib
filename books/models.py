from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from cloudinary.models import CloudinaryField


class Role(models.Model):
    """RBAC 角色與細粒度權限表"""
    ROLE_CHOICES = [
        ('super_admin', '超級管理員 Super Admin'),
        ('librarian_admin', '館長 Librarian Admin'),
        ('librarian', '圖書館員 Librarian'),
        ('procurement', '採購人員 Procurement'),
        ('member', '讀者 Member'),
        ('guest', '訪客 Guest'),
    ]
    code = models.CharField(max_length=30, unique=True, choices=ROLE_CHOICES, verbose_name="角色代碼")
    name = models.CharField(max_length=50, verbose_name="角色名稱")

    # 書籍模組權限
    book_read = models.BooleanField(default=True, verbose_name="book.read")
    book_create = models.BooleanField(default=False, verbose_name="book.create")
    book_update = models.BooleanField(default=False, verbose_name="book.update")
    book_delete = models.BooleanField(default=False, verbose_name="book.delete")

    # 借閱模組權限
    borrow_create = models.BooleanField(default=False, verbose_name="borrow.create")
    borrow_return = models.BooleanField(default=False, verbose_name="borrow.return")
    borrow_approve = models.BooleanField(default=False, verbose_name="borrow.approve")

    # 採購與系統權限
    procurement_manage = models.BooleanField(default=False, verbose_name="procurement.manage")
    system_settings = models.BooleanField(default=False, verbose_name="system.settings")

    def has_perm(self, perm_string: str) -> bool:
        """將 'book.create' 轉換為欄位名稱 'book_create' 進行驗證"""
        field_name = perm_string.replace('.', '_')
        return getattr(self, field_name, False)

    def __str__(self):
        return f"{self.name} ({self.code})"


class UserProfile(models.Model):
    """擴充 Django 內建 User 綁定單一 RBAC 角色"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name='users')

    def has_rbac_perm(self, perm_string: str) -> bool:
        return self.role.has_perm(perm_string)

    def __str__(self):
        return f"{self.user.username} - {self.role.name}"


class Book(models.Model):
    """館藏書籍模型（整合 Cloudinary 封面）"""
    title = models.CharField(max_length=200, verbose_name="書名")
    author = models.CharField(max_length=100, verbose_name="作者")
    isbn = models.CharField(max_length=13, unique=True, verbose_name="ISBN")
    cover = CloudinaryField('cover', folder='library_covers', blank=True, null=True)
    is_available = models.BooleanField(default=True, verbose_name="可借閱狀態")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.isbn})"


class BorrowRecord(models.Model):
    """借閱與歸還紀錄"""
    STATUS_CHOICES = [
        ('pending', '待審核'),
        ('borrowed', '借閱中'),
        ('returned', '已歸還'),
    ]
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="records")
    borrower_name = models.CharField(max_length=100, verbose_name="借閱人姓名")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='borrowed')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    borrowed_at = models.DateTimeField(default=timezone.now)
    returned_at = models.DateTimeField(null=True, blank=True)


class AuditLog(models.Model):
    """重要操作稽核日誌"""
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="操作者")
    actor_role = models.CharField(max_length=50, default="Guest", verbose_name="操作者角色")
    action = models.CharField(max_length=100, verbose_name="操作類型")
    target_model = models.CharField(max_length=50, verbose_name="目標模組")
    target_id = models.CharField(max_length=50, blank=True, verbose_name="目標ID")
    changes = models.TextField(verbose_name="變更內容詳情")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="操作時間")

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.actor} - {self.action}"