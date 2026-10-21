#!/bin/sh
# Builds dist/DailyPaper.app with py2app, using the project's uv environment.
# py2app refuses to build next to a pyproject.toml that declares dependencies,
# so build from a staging copy that has only the package and setup_app.py.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/build/stage"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$ROOT/dailypaper" "$ROOT/setup_app.py" "$ROOT/app_entry.py" "$STAGE/"
cd "$STAGE"
uv run --project "$ROOT" python setup_app.py py2app --dist-dir "$ROOT/dist"
echo "Built $ROOT/dist/DailyPaper.app"
