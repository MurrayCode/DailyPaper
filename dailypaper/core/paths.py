import os
import sys
from pathlib import Path

APP_NAME = "DailyPaper"


def data_dir() -> Path:
    """Where the database and config live. DAILYPAPER_HOME overrides (used by tests)."""
    override = os.environ.get("DAILYPAPER_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "dailypaper"
    path.mkdir(parents=True, exist_ok=True)
    return path
