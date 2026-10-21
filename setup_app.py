"""Builds DailyPaper.app with py2app.

    ./scripts/build_app.sh          # -> dist/DailyPaper.app (builds from a staging copy)
"""

from setuptools import setup

setup(
    name="DailyPaper",
    app=["app_entry.py"],
    options={
        "py2app": {
            "packages": ["dailypaper", "anthropic", "keyring", "rumps"],
            "plist": {
                "CFBundleName": "DailyPaper",
                "CFBundleIdentifier": "com.dailypaper.app",
                "CFBundleShortVersionString": "0.1.0",
                "LSUIElement": True,  # menu bar only, no Dock icon
            },
        }
    },
)
