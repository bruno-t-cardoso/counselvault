import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

if DEBUG:
    SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-secret")
    ALLOWED_HOSTS = ["*"]
else:
    # Outside local development nothing falls back to a default.
    try:
        SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
        ALLOWED_HOSTS = os.environ["DJANGO_ALLOWED_HOSTS"].split(",")
    except KeyError as missing:
        raise ImproperlyConfigured(
            f"{missing.args[0]} must be set when DJANGO_DEBUG is not 1"
        ) from None
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.postgres",
    "rest_framework",
    "vault",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"


def _database(user, password, **extra):
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "counselvault"),
        "USER": user,
        "PASSWORD": password,
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        **extra,
    }


# The API and worker connect as DB_APP_USER, which does not own the tables and so
# cannot alter the schema, the row-level security policies or the audit triggers.
# Migrations run as the owner: `manage.py migrate --database owner`.
DB_APP_USER = os.environ.get("DB_APP_USER", "counselvault_app")
DB_OWNER_USER = os.environ.get("DB_OWNER_USER", "counselvault_owner")
DATABASES = {
    "default": _database(DB_APP_USER, os.environ.get("DB_APP_PASSWORD", "app-dev-password")),
    "owner": _database(
        DB_OWNER_USER,
        os.environ.get("DB_OWNER_PASSWORD", "owner-dev-password"),
        TEST={"MIRROR": "default"},  # same database, different role
    ),
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": "600/min"},
}

# Object storage. Credentials and region come from the standard AWS environment
# variables. S3_ENDPOINT_URL is only set for a local S3 stand-in.
S3_BUCKET = os.environ.get("S3_BUCKET", "counselvault-dev")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL") or None
S3_URL_TTL_SECONDS = 300

# Upload events: S3 notifies this SQS queue, and the worker reads from it.
UPLOAD_EVENTS_QUEUE = os.environ.get("UPLOAD_EVENTS_QUEUE", "counselvault-uploads")
SQS_ENDPOINT_URL = os.environ.get("SQS_ENDPOINT_URL") or S3_ENDPOINT_URL
# How long a message the worker could not handle yet stays hidden before a retry.
UPLOAD_EVENT_RETRY_SECONDS = 5
# Files larger than this are not downloaded for text extraction.
TEXT_EXTRACTION_MAX_BYTES = 20 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
