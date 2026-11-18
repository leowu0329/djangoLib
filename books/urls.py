from django.urls import path
from . import views

app_name = "books"

urlpatterns = [
    # 首頁與書目清單（同時相容 index 與舊版 book_list 路由名稱）
    path("", views.index, name="index"),
    path("list/", views.index, name="book_list"),

    # 1. 圖書管理流程：新增書目與館藏副本
    path("add/", views.add_book, name="add_book"),

    # 2. 借閱與歸還流程
    path("borrow/", views.borrow_copy, name="borrow_copy"),
    path("return/", views.return_copy, name="return_copy"),

    # 3. 預約與候補流程 & 館藏損壞異常處理
    path("reserve/<int:book_id>/", views.reserve_book, name="reserve_book"),
    path("damage/", views.report_damaged, name="report_damaged"),
]