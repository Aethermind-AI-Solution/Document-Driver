from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
# Local development uses the project .env as the source of truth. Container
# deployments do not include this file and continue to use injected secrets.
load_dotenv(ROOT / ".env", override=True)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'database' / 'document_intelligence.db'}")
# Keep the documented sqlite:///./database/... form stable regardless of whether
# uvicorn is launched from the project root or the backend directory.
if DATABASE_URL.startswith("sqlite:///./"):
    DATABASE_URL = f"sqlite:///{ROOT / DATABASE_URL.removeprefix('sqlite:///./')}"
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", ROOT / "uploads"))
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", ROOT / "exports"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
# Comma-separated list of browser origins allowed to call the API. Defaults to
# local dev; in production set CORS_ORIGINS to the deployed frontend URL(s),
# e.g. "https://aethermind.vercel.app".
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "20"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")  # "local" | "s3"
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
S3_REGION = os.getenv("S3_REGION", "auto")  # "auto" for R2; Supabase/B2 need their real region
for directory in (UPLOAD_DIR, EXPORT_DIR):
    directory.mkdir(parents=True, exist_ok=True)
# Fresh deployments start with an empty tree, so make sure the SQLite database
# directory exists before SQLAlchemy tries to open the file.
if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
JWT_SECRET = os.getenv("JWT_SECRET", "dev-insecure-secret-change-me")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CLASSIFIER_MODEL = os.getenv("CLASSIFIER_MODEL", "gpt-4o-mini")
PIPELINE_CONCURRENCY = int(os.getenv("PIPELINE_CONCURRENCY", "5"))
PIPELINE_STAGE_TIMEOUT = int(os.getenv("PIPELINE_STAGE_TIMEOUT", "60"))
MCP_API_TOKEN = os.getenv("MCP_API_TOKEN", "")          # empty ⇒ MCP server disabled
MCP_SERVICE_ROLE = os.getenv("MCP_SERVICE_ROLE", "reviewer")
LEARNING_ENABLED = os.getenv("LEARNING_ENABLED", "true").lower() in ("1", "true", "yes")
LEARNING_MAX_HINTS_PER_FIELD = int(os.getenv("LEARNING_MAX_HINTS_PER_FIELD", "3"))
LEARNING_MAX_HINTS = int(os.getenv("LEARNING_MAX_HINTS", "20"))
SCHEMA_AUTHOR_ENABLED = os.getenv("SCHEMA_AUTHOR_ENABLED", "true").lower() in ("1", "true", "yes")
AUTO_APPROVE_ENABLED = os.getenv("AUTO_APPROVE_ENABLED", "false").lower() in ("1", "true", "yes")
LOG_FORMAT = os.getenv("LOG_FORMAT", "json")
STUCK_PROCESSING_MINUTES = int(os.getenv("STUCK_PROCESSING_MINUTES", "15"))
METRICS_RECENT_N = int(os.getenv("METRICS_RECENT_N", "200"))
DEFAULT_ORG_ID = int(os.getenv("DEFAULT_ORG_ID", "1"))


def check_production_config():
    """Refuse to boot in prod (non-sqlite DB) with an insecure/blank JWT secret."""
    if not DATABASE_URL.startswith("sqlite") and JWT_SECRET in ("", "dev-insecure-secret-change-me"):
        raise RuntimeError("JWT_SECRET must be set to a strong value in production (non-sqlite DATABASE_URL).")
