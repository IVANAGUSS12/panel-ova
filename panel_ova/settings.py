import os
from pathlib import Path
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

load_dotenv()


def get_list_env(name, default=None):
    value = os.getenv(name, "")
    if value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return list(default or [])


def get_int_env(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_bool_env(name, default=False):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_path_env(name, default):
    value = os.getenv(name, "")
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path

# ===========================
# BASE DIR
# ===========================
BASE_DIR = Path(__file__).resolve().parent.parent

# ===========================
# CONFIG GENERAL
# ===========================
DEBUG = get_bool_env('DEBUG', False)
SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'dev-insecure-key-only-for-local-debug'
    else:
        raise ImproperlyConfigured('SECRET_KEY es obligatoria cuando DEBUG=False.')

AUTO_REFRESH_AGENDAS = get_bool_env('AUTO_REFRESH_AGENDAS', True)
AUTO_REFRESH_AGENDAS_INTERVAL_MINUTES = get_int_env('AUTO_REFRESH_AGENDAS_INTERVAL_MINUTES', 20)

# Hosts permitidos. Se pueden actualizar desde .env sin tocar el código.
ALLOWED_HOSTS = get_list_env(
    'ALLOWED_HOSTS',
    default=[
        'localhost',
        '127.0.0.1',
        '10.1.42.70',
        '.trycloudflare.com',
        'panel.oficinavirtualcemic.com',
    ],
)

CSRF_TRUSTED_ORIGINS = get_list_env(
    'CSRF_TRUSTED_ORIGINS',
    default=[
        'http://localhost',
        'http://127.0.0.1',
        'http://10.1.42.70',
        'https://*.trycloudflare.com',
        'https://panel.oficinavirtualcemic.com',
    ],
)
# ===========================
# APPS
# ===========================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'accounts',
    'core',
    'compressor',
]

# ===========================
# MIDDLEWARE
# ===========================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'core.middleware.SlowQueryLoggingMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.CurrentUserMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.AuditMiddleware',
]

# ===========================
# URLS / WSGI
# ===========================
ROOT_URLCONF = 'panel_ova.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
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

WSGI_APPLICATION = 'panel_ova.wsgi.application'

# ===========================
# DATABASE
# ===========================
_database_url = os.getenv('DATABASE_URL')
_pg_db = os.getenv('POSTGRES_DB')
if _database_url:
    try:
        import dj_database_url
    except ImportError as exc:
        raise ImproperlyConfigured(
            'DATABASE_URL requiere instalar dj-database-url.'
        ) from exc
    DATABASES = {'default': dj_database_url.parse(_database_url, conn_max_age=600)}
elif _pg_db:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': _pg_db,
            'USER': os.getenv('POSTGRES_USER', ''),
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', ''),
            'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ===========================
# PASSWORD VALIDATION
# ===========================
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
]

# ===========================
# LOCALIZACIÓN
# ===========================
LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'
USE_I18N = True
USE_TZ = True

# ===========================
# STATIC & MEDIA
# ===========================
STATIC_URL = os.getenv('STATIC_URL', '/static/')
STATIC_ROOT = get_path_env('STATIC_ROOT', BASE_DIR / 'staticfiles')
STATICFILES_DIRS = []

# Use WhiteNoise to serve compressed static files with cache-friendly names.
if DEBUG:
    _STATICFILES_STORAGE_BACKEND = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    _STATICFILES_STORAGE_BACKEND = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': _STATICFILES_STORAGE_BACKEND,
    },
}

WHITENOISE_ROOT = STATIC_ROOT
WHITENOISE_MIMETYPES = {
    '.css': 'text/css',
    '.js': 'application/javascript',
}

# Staticfiles finders: include CompressorFinder so django-compressor can
# locate and process CSS/JS blocks in templates.
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'compressor.finders.CompressorFinder',
]

# django-compressor settings
# Desactivado temporalmente en desarrollo para evitar conflictos
COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False
COMPRESS_OUTPUT_DIR = 'CACHE'
COMPRESS_URL = STATIC_URL
COMPRESS_ROOT = STATIC_ROOT

MEDIA_URL = os.getenv('MEDIA_URL', '/media/')
MEDIA_ROOT = get_path_env('MEDIA_ROOT', BASE_DIR / 'media')

# ===========================
# LOGIN
# ===========================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'core:dashboard'
LOGOUT_REDIRECT_URL = 'accounts:login'

# Django Debug Toolbar / Silk: añadimos condicionalmente sólo si los paquetes
# están instalados en el entorno para evitar errores en ambientes sin dev-tools.
INTERNAL_IPS = [
    '127.0.0.1',
    '::1',
]
# ===========================
# CLOUDFLARE / HTTPS SETTINGS
# ===========================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = get_bool_env('SECURE_SSL_REDIRECT', False)
SECURE_HSTS_SECONDS = get_int_env('SECURE_HSTS_SECONDS', 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool_env('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = get_bool_env('SECURE_HSTS_PRELOAD', False)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG


# Logging minimal para consultas lentas (registrador: 'slow_queries')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'slow_file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': str(get_path_env('SLOW_QUERY_LOG_PATH', BASE_DIR / 'slow_queries.log')),
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'slow_queries': {
            'handlers': ['slow_file'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# ===========================
# EMAIL
# ===========================
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = get_int_env('EMAIL_PORT', 587)
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = get_bool_env('EMAIL_USE_TLS', True)
EMAIL_USE_SSL = get_bool_env('EMAIL_USE_SSL', False)
EMAIL_TIMEOUT = get_int_env('EMAIL_TIMEOUT', 30)
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'panel-ova@localhost')
SERVER_EMAIL = os.getenv('SERVER_EMAIL', DEFAULT_FROM_EMAIL)
PATIENT_TRACKING_FROM_EMAIL = os.getenv(
    'PATIENT_TRACKING_FROM_EMAIL',
    DEFAULT_FROM_EMAIL or 'oficinavirtualdeautorizaciones@cemic.edu.ar',
)
ANESTHESIA_CONTACT_EMAIL = os.getenv(
    'ANESTHESIA_CONTACT_EMAIL',
    'consultorioanestesia@cemic.edu.ar',
)

ADMISSION_EMAIL_BACKEND = os.getenv('ADMISSION_EMAIL_BACKEND', EMAIL_BACKEND)
ADMISSION_EMAIL_HOST = os.getenv('ADMISSION_EMAIL_HOST', EMAIL_HOST)
ADMISSION_EMAIL_PORT = get_int_env('ADMISSION_EMAIL_PORT', EMAIL_PORT)
ADMISSION_EMAIL_HOST_USER = os.getenv('ADMISSION_EMAIL_HOST_USER', EMAIL_HOST_USER)
ADMISSION_EMAIL_HOST_PASSWORD = os.getenv('ADMISSION_EMAIL_HOST_PASSWORD', EMAIL_HOST_PASSWORD)
ADMISSION_EMAIL_USE_TLS = get_bool_env('ADMISSION_EMAIL_USE_TLS', EMAIL_USE_TLS)
ADMISSION_EMAIL_USE_SSL = get_bool_env('ADMISSION_EMAIL_USE_SSL', EMAIL_USE_SSL)
ADMISSION_EMAIL_TIMEOUT = get_int_env('ADMISSION_EMAIL_TIMEOUT', EMAIL_TIMEOUT)
ADMISSION_DEFAULT_FROM_EMAIL = os.getenv(
    'ADMISSION_DEFAULT_FROM_EMAIL',
    ADMISSION_EMAIL_HOST_USER or DEFAULT_FROM_EMAIL,
)

# ===========================
# WHATSAPP CLOUD API
# ===========================
WHATSAPP_CLOUD_API_VERSION = os.getenv('WHATSAPP_CLOUD_API_VERSION', 'v20.0')
WHATSAPP_CLOUD_PHONE_NUMBER_ID = os.getenv('WHATSAPP_CLOUD_PHONE_NUMBER_ID', '')
WHATSAPP_CLOUD_ACCESS_TOKEN = os.getenv('WHATSAPP_CLOUD_ACCESS_TOKEN', '')
WHATSAPP_CLOUD_TIMEOUT = get_int_env('WHATSAPP_CLOUD_TIMEOUT', 20)
WHATSAPP_TRACKING_TEMPLATE_NAME = os.getenv('WHATSAPP_TRACKING_TEMPLATE_NAME', '')
WHATSAPP_TRACKING_TEMPLATE_LANGUAGE = os.getenv('WHATSAPP_TRACKING_TEMPLATE_LANGUAGE', 'es_AR')
