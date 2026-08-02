import environ

from core.files.file_upload_client import FILE_UPLOADER_CLASSES

env = environ.Env()
environ.Env.read_env()

# Database

DATABASE_URL = f"postgresql+asyncpg://{env.str('POSTGRES_USER')}:{env.str('POSTGRES_PASSWORD')}@{env.str('POSTGRES_HOST')}/{env.str('POSTGRES_DATABASE')}"
TEST_DATABASE_URL = f"postgresql+asyncpg://{env.str('POSTGRES_USER')}:{env.str('POSTGRES_PASSWORD')}@{env.str('POSTGRES_HOST')}/test_db"

# External APIs

NUTRITION_API_URL = f"{env.str('NUTRITION_API_URL')}"
NUTRITION_APIKEY = f"{env.str('NUTRITION_APIKEY')}"

# Cryptography

ALGORITHM = env.str("ALGORITHM")
SECRET_KEY = env.str("SECRET_KEY")

# Celery
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL")

# App Behavior
ACCESS_TOKEN_EXPIRE_MINUTES = env.int("ACCESS_TOKEN_EXPIRE_MINUTES", default=60)
APP_LOCATION = env.str("APP_LOCATION")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")
PLAYWRIGHT_ENABLED = env.bool("PLAYWRIGHT_ENABLED", default=False)

CLOUD_PROVIDER_REQUIRED_VARS = {
    "local": [],
    "test": [],
    "google": ["GCLOUD_KEY_FILE", "GCLOUD_BUCKET_NAME"],
    "aws": ["AWS_BUCKET_NAME", "AWS_REGION"],
}

CLOUD_PROVIDER = env.str("CLOUD_PROVIDER")

if CLOUD_PROVIDER not in CLOUD_PROVIDER_REQUIRED_VARS:
    raise environ.ImproperlyConfigured(
        f"CLOUD_PROVIDER must be one of {sorted(CLOUD_PROVIDER_REQUIRED_VARS)}, got {CLOUD_PROVIDER!r}"
    )

# env() rather than env.str() for optional values: environ types str()'s default as
# `str | NoValue`, so a None default is a type error even though it works at runtime.
missing_vars = [var for var in CLOUD_PROVIDER_REQUIRED_VARS[CLOUD_PROVIDER] if not env(var, default=None)]
if missing_vars:
    raise environ.ImproperlyConfigured(
        f"CLOUD_PROVIDER={CLOUD_PROVIDER!r} requires env vars: {', '.join(missing_vars)}"
    )

# GCLOUD
GCLOUD_KEY_FILE = env("GCLOUD_KEY_FILE", default=None)
GCLOUD_BUCKET_NAME = env("GCLOUD_BUCKET_NAME", default=None)

# AWS
AWS_BUCKET_NAME = env("AWS_BUCKET_NAME", default=None)
AWS_REGION = env("AWS_REGION", default=None)


# STORAGE
FILE_MAX_UPLOAD_SIZE = env.int("FILE_MAX_UPLOAD_SIZE")
ALLOWED_EXTENSIONS = env.list("ALLOWED_EXTENSIONS")
FILE_URL_EXPIRATION_SECONDS = env.int("FILE_URL_EXPIRATION_SECONDS", default=3600)

FILE_UPLOADER_CLASS = FILE_UPLOADER_CLASSES.get(CLOUD_PROVIDER, None)
