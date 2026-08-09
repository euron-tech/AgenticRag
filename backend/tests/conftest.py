"""Test configuration.

Settings are read at import time, so the environment must be populated before
anything under `app` is imported. These are placeholders — no test in this
suite reaches Supabase or OpenAI.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-not-a-real-one")
os.environ.setdefault("SUPABASE_DB_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")
