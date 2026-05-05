"""
Test-specific settings. Inherits from the main settings module and overrides
only what is necessary for the test environment.
"""

import os
import tempfile
from pathlib import Path

from design_workflow_backend.settings import *  # noqa: F401, F403

# -- Overrides ---------------------------------------------------------------

DEBUG = True

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Disable simple_history in tests for speed
INSTALLED_APPS = [
    app for app in INSTALLED_APPS if app != "simple_history"  # noqa: F405
]
MIDDLEWARE = [
    mw
    for mw in MIDDLEWARE
    if mw != "simple_history.middleware.HistoryRequestMiddleware"  # noqa: F405
]

# Use in-memory email backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Celery eager mode so tasks run synchronously in tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Disable throttling in tests to avoid rate-limit flakiness
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []  # noqa: F405
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "anon": "10000/minute",
    "user": "10000/minute",
    "login": "10000/minute",
    "password_reset": "10000/minute",
}

# Use a test-only secret key with sufficient length for JWT signing.
SECRET_KEY = "design_workflow-test-secret-key-2026-abcdef-123456"

# Use in-memory channel layer (no Redis dependency for tests)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# Static CORS for tests
CORS_ORIGIN_WHITELIST = ("http://localhost:3004",)

ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http"

TEST_DATABASE_PATH = os.environ.get(
    "DESIGN_WORKFLOW_TEST_DB",
    str(Path(tempfile.gettempdir()) / "design_workflow_test_db.sqlite3"),
)
if TEST_DATABASE_PATH != ":memory:":
    Path(TEST_DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": TEST_DATABASE_PATH,
        "TEST": {
            "NAME": TEST_DATABASE_PATH,
        },
    }
}
