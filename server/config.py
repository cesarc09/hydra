import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
AUTH_TOKEN = os.getenv("HYDRA_AUTH_TOKEN", "")
DB_PATH = os.getenv("HYDRA_DB_PATH", str(BASE_DIR / "hydra.db"))
EDITORS_PATH = os.getenv("HYDRA_EDITORS_PATH", str(BASE_DIR / "editors.json"))
