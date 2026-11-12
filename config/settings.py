import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Development defaults only; override through the environment anywhere else.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"] if DEBUG else os.environ.get("ALLOWED_HOSTS", "").split(",")

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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "counselvault"),
        # Must be a non-superuser: superusers bypass row-level security.
        "USER": os.environ.get("DB_USER", "counselvault"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "counselvault"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
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
