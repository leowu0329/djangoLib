from django.contrib import admin
from django.urls import path, include
from books import views as book_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    # 提供無命名空間相容別名，確保原有模板 {% url 'reserve_book' %} 正常運作
    path('books/<int:book_id>/reserve/', book_views.reserve_book_view, name='reserve_book'),
    path('copies/<int:copy_id>/borrow/', book_views.borrow_copy_view, name='borrow_copy'),
    path('copies/<int:copy_id>/return/', book_views.return_copy_view, name='return_copy'),
    path('', include('books.urls')),
]