"""Installs a per-user LaunchAgent so the menu bar app starts at login."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from ..core.paths import data_dir

LABEL = "com.dailypaper.agent"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def program_arguments() -> list[str]:
    if getattr(sys, "frozen", False):  # running inside the py2app bundle
        return [sys.executable]
    return [sys.executable, "-m", "dailypaper", "app"]


def install() -> Path:
    log = str(data_dir() / "app.log")
    plist = {
        "Label": LABEL,
        "ProgramArguments": program_arguments(),
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",  # only in a GUI login session
        "ProcessType": "Interactive",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    _bootout()  # replace any previous version
    with PLIST.open("wb") as fh:
        plistlib.dump(plist, fh)
    subprocess.run(["launchctl", "bootstrap", _domain(), str(PLIST)], check=True)
    return PLIST


def uninstall() -> None:
    _bootout()
    PLIST.unlink(missing_ok=True)


def is_installed() -> bool:
    return PLIST.exists()


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _bootout() -> None:
    if PLIST.exists():
        subprocess.run(["launchctl", "bootout", _domain(), str(PLIST)], capture_output=True)
