# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# Explicitly find .env wherever the script runs from
env_path = Path(__file__).parent / ".env"
if not env_path.exists():
    env_path = Path.cwd() / ".env"

load_dotenv(dotenv_path=env_path)

# ─────────────────────────────────────────────
# API KEYS
# ─────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

if not GROQ_API_KEY:
    raise RuntimeError(
        "\n[MAROS] No Groq API key found.\n"
        "Add it to your .env file:  GROQ_API_KEY=gsk_...\n"
    )

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"

UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
# CHIPPER SETTINGS
# ─────────────────────────────────────────────

WHISPER_MODEL     = os.getenv("WHISPER_MODEL", "base")
MAX_MODULES       = int(os.getenv("MAX_MODULES", 4))
MIN_CLIP_DURATION = float(os.getenv("MIN_CLIP_DURATION", 5.0))
TRANSCRIPT_CAP    = int(os.getenv("TRANSCRIPT_CAP", 30000))

# ─────────────────────────────────────────────
# GROQ MODEL SETTINGS
# ─────────────────────────────────────────────

GROQ_CHAT_MODEL    = "llama-3.3-70b-versatile"
GROQ_WHISPER_MODEL = "whisper-large-v3"
GROQ_BASE_URL      = "https://api.groq.com/openai/v1"

GROQ_HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json"
}

# ─────────────────────────────────────────────
# SERVER SETTINGS
# ─────────────────────────────────────────────

HOST         = os.getenv("HOST", "0.0.0.0")
PORT         = int(os.getenv("PORT", 8000))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")