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
DEMO_ACCESS_TOKEN = os.getenv("DEMO_ACCESS_TOKEN", "")   # empty ⇒ gate disabled
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "20"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
for directory in (UPLOAD_DIR, EXPORT_DIR):
    directory.mkdir(parents=True, exist_ok=True)
# Fresh deployments start with an empty tree, so make sure the SQLite database
# directory exists before SQLAlchemy tries to open the file.
if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
