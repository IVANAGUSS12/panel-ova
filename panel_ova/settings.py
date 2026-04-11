import os
from pathlib import Path
from dotenv import load_dotenv

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

# ===========================
# BASE DIR
# ===========================
BASE_DIR = Path(__file__).resolve().parent.parent

# ===========================
# CONFIG GENERAL
# ===========================
# ⚠️ ADVERTENCIA: Cambiar SECRET_KEY en producción usando variable de entorno
SECRET_KEY = os.getenv('SECRET_KEY', 'insecure-key')

# ⚠️ ADVERTENCIA: Cambiar a False en producción
DEBUG = True   # Dejalo así hasta que funcione Cloudflare

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
    # ⚠ ESTE BLOQUEABA TODO LO EXTERNO — LO SACAMOS
    # 'core.middleware.ExternalQRLockdownMiddleware',

    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'core.middleware.SlowQueryLoggingMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.CurrentUserMiddleware',  # Captura usuario para historial
    # (debug toolbar middleware se insertará dinámicamente solo en DEBUG
    #  si el paquete está instalado)
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware', 

    # Dejalo, no bloquea externo
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
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'core' / 'static',
]

# Use WhiteNoise to serve compressed static files with cache-friendly names
# In production this should be used together with `collectstatic` and
# a webserver or CDN that serves files from `STATIC_ROOT`.
# En desarrollo, usar el storage estándar para evitar problemas
if DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

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

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

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
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False


# Logging minimal para consultas lentas (registrador: 'slow_queries')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'slow_file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': str(BASE_DIR / 'slow_queries.log'),
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
