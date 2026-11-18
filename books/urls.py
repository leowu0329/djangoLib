from django.urls import path
from . import views

urlpatterns = [
    path('', views.book_list, name='book_list'),
    path('switch-role/<str:role_code>/', views.switch_role, name='switch_role'),
    path('books/add/', views.add_book, name='add_book'),
    path('books/<int:book_id>/update/', views.update_book, name='update_book'),
    path('books/<int:book_id>/delete/', views.delete_book, name='delete_book'),
    path('books/<int:book_id>/borrow/', views.borrow_book, name='borrow_book'),
    path('books/<int:book_id>/return/', views.return_book, name='return_book'),
    path('system/settings/', views.system_settings, name='system_settings'),
]
