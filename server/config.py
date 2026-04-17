import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
AUTH_TOKEN = os.getenv("HYDRA_AUTH_TOKEN", "")
ALLOW_NO_AUTH = os.getenv("HYDRA_ALLOW_NO_AUTH", "") == "1"
BIND_HOST = os.getenv("HYDRA_BIND_HOST", "127.0.0.1")
PUBLIC_ORIGIN = os.getenv("HYDRA_PUBLIC_ORIGIN", "")
DB_PATH = os.getenv("HYDRA_DB_PATH", str(BASE_DIR / "hydra.db"))
EDITORS_PATH = os.getenv("HYDRA_EDITORS_PATH", str(BASE_DIR / "editors.json"))
