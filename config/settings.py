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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
