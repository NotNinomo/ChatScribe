"""ChatScribe - local push-to-talk speech-to-text for WoW Forever chat."""

import os
import sys
from pathlib import Path

__version__ = "1.1.0"

FROZEN = getattr(sys, "frozen", False)
# Packaged exe: user files (config, word lists, models, data) live next to ChatScribe.exe.
# From source: the project folder.
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
LEXICON_DIR = APP_DIR / "lexicon"
DATA_DIR = APP_DIR / "data"
MODELS_DIR = APP_DIR / "models"

DATA_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)

# Keep HuggingFace downloads inside the app folder instead of the user profile.
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
