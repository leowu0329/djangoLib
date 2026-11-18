import os
from pathlib import Path
from dotenv import load_dotenv

# 1. 確認 BASE_DIR 指向與 manage.py 同層的專案根目錄
BASE_DIR = Path(__file__).resolve().parent.parent

# 2. 指定讀取根目錄下的 .env，並使用 override=True 強制覆蓋任何殘留的系統變數
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path, override=True)

# 從環境變數讀取 Django 核心設定
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'fallback-dev-secret-key')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # 第三方套件
    'cloudinary_storage',
    'cloudinary',
    # 本地應用
    'books',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'src.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# 從根目錄 .env 讀取 Cloudinary 雲端圖片庫金鑰
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
    'API_KEY': os.environ.get('CLOUDINARY_API_KEY', ''),
    'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET', ''),
}
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

LANGUAGE_CODE = 'zh-hant'
TIME_ZONE = 'Asia/Taipei'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# 3. 讀取 Cloudinary 憑證（將預設值改為空字串 ''，確保不再出現 'demo'） CLOUDINARY_STORAGE = { 'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME', ''), 'API_KEY': os.environ.get('CLOUDINARY_API_KEY', ''), 'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET', ''), } 
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
