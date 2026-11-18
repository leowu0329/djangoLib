from django.urls import path
from . import views

app_name = 'books'

urlpatterns = [
    path('', views.book_list_view, name='book_list'),
    path('copies/<int:copy_id>/borrow/', views.borrow_copy_view, name='borrow_copy'),
    path('copies/<int:copy_id>/return/', views.return_copy_view, name='return_copy'),
    path('books/<int:book_id>/reserve/', views.reserve_book_view, name='reserve_book'),
]