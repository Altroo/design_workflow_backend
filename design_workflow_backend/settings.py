"""Django settings for the design_workflow project."""

import os
from datetime import timedelta
from pathlib import Path

from celery.schedules import crontab
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY")

DEBUG = config("DEBUG", cast=bool, default=False)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost").split(",")

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS", default="http://127.0.0.1,http://localhost"
).split(",")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "daphne",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.postgres",
    "corsheaders",
    "channels",
    "ws.apps.WsConfig",
    "rest_framework",
    "rest_framework_simplejwt",
    "dj_rest_auth",
    "simple_history",
    "account.apps.AccountConfig",
    "design_workflow.apps.DesignWorkflowConfig",
    "axes",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "axes.middleware.AxesMiddleware",
]

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

ROOT_URLCONF = "design_workflow_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS", default="http://localhost:3004"
).split(",")
CORS_ALLOW_CREDENTIALS = True

ASGI_APPLICATION = "design_workflow_backend.asgi.application"
WSGI_APPLICATION = "design_workflow_backend.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": config("POSTGRES_DB", "postgres"),
        "USER": config("POSTGRES_USER", "postgres"),
        "PASSWORD": config("POSTGRES_PASSWORD", ""),
        "HOST": config("POSTGRES_HOST", "db"),
        "PORT": config("POSTGRES_PORT", "5432"),
        # With ASGI (Daphne) each request runs in its own thread;
        # persistent connections (CONN_MAX_AGE > 0) pile up and
        # quickly exhaust PostgreSQL's max_connections limit.
        # Use 0 so each request closes its connection when done.
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "fr"
LANGUAGES = [
    ("fr", "Français"),
    ("en", "English"),
]

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

TIME_ZONE = "Africa/Casablanca"

USE_I18N = True

USE_TZ = True

STATIC_URL = "/static/"
STATIC_PATH = "static"
STATIC_ROOT = os.path.join(BASE_DIR, "static")
STATICFILES_DIRS = ()
MEDIA_ROOT = os.path.join(BASE_DIR, "media")
MEDIA_URL = "/media/"

DATA_UPLOAD_MAX_MEMORY_SIZE = 20971520  # 20MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 20971520  # 20MB
MAX_BASE64_IMAGE_SIZE = 15 * 1024 * 1024  # 15MB base64

AUTH_USER_MODEL = "accounts.CustomUser"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
SITE_ID = 1

REST_FRAMEWORK = dict(
    DEFAULT_AUTHENTICATION_CLASSES=("dj_rest_auth.jwt_auth.JWTAuthentication",),
    DEFAULT_FILTER_BACKENDS=["django_filters.rest_framework.DjangoFilterBackend"],
    DEFAULT_PERMISSION_CLASSES=("rest_framework.permissions.IsAuthenticated",),
    DEFAULT_PAGINATION_CLASS="rest_framework.pagination.PageNumberPagination",
    PAGE_SIZE=20,
    DEFAULT_RENDERER_CLASSES=("rest_framework.renderers.JSONRenderer",),
    EXCEPTION_HANDLER="design_workflow_backend.utils.api_exception_handler",
    NON_FIELD_ERRORS_KEY="error",
    TOKEN_MODEL=None,
    DEFAULT_THROTTLE_CLASSES=[
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    DEFAULT_THROTTLE_RATES={
        "anon": "60/minute",
        "user": "200/minute",
        "login": "5/minute",
        "password_reset": "3/minute",
    },
)

REST_AUTH = {
    "USE_JWT": True,
    "JWT_AUTH_RETURN_EXPIRATION": True,
    "JWT_AUTH_COOKIE": "design_workflow-jwt-access",
    "JWT_AUTH_REFRESH_COOKIE": "design_workflow-jwt-refresh",
    "TOKEN_MODEL": None,
    "OLD_PASSWORD_FIELD_ENABLED": True,
    "JWT_AUTH_HTTPONLY": True,
    "LOGOUT_ON_PASSWORD_CHANGE": True,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "BLACKLIST_AFTER_ROTATION": True,
    "ROTATE_REFRESH_TOKENS": True,
}

REDIS_HOST = config("REDIS_HOST")
REDIS_PORT = config("REDIS_PORT")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [(REDIS_HOST, REDIS_PORT)],
        },
    },
}

# Celery uses Redis DB 1 (facturation uses DB 0)
CELERY_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/1"
CELERY_RESULT_BACKEND = f"redis://{REDIS_HOST}:{REDIS_PORT}/1"
CELERY_BEAT_SCHEDULE = {
    "design-workflow-due-task-notifications": {
        "task": "design_workflow.tasks.generate_due_task_notifications",
        "schedule": crontab(minute=0, hour=7),
    },
    "design-workflow-notification-digests": {
        "task": "design_workflow.tasks.generate_notification_digests",
        "schedule": crontab(minute=30, hour=7),
    },
}

ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_MAX_EMAIL_ADDRESSES = 1
ACCOUNT_DEFAULT_HTTP_PROTOCOL = config("ACCOUNT_DEFAULT_HTTP_PROTOCOL", default="https")

EMAIL_BACKEND = config(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=30, cast=int)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="")
SERVER_EMAIL = config("SERVER_EMAIL", default="")

API_URL = config("API_URL")
FRONTEND_URL = config("FRONTEND_URL", default="")

# ──────────────────────────────────────────────
# Security settings
# ──────────────────────────────────────────────

# Cookie security
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# HTTPS enforcement (handled by nginx in production, but belt-and-suspenders)
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=not DEBUG, cast=bool)
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True

# ──────────────────────────────────────────────
# Django Axes — brute-force protection
# ──────────────────────────────────────────────
AXES_FAILURE_LIMIT = 5  # Lock after 5 failed attempts
AXES_COOLOFF_TIME = timedelta(minutes=15)  # Lockout duration
AXES_RESET_ON_SUCCESS = True  # Reset counter on successful login
AXES_LOCKOUT_CALLABLE = None  # Use default 403 response
# Get real IP from X-Forwarded-For header (behind nginx proxy)
AXES_IPWARE_PROXY_COUNT = 1
AXES_IPWARE_PROXY_ORDER = "left-most"
