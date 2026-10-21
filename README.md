# DailyPaper

Every day: one industry whitepaper, picked by Claude from your interests and your 1–5 star
ratings, plus 3 blog posts on the same interests. It never suggests the same paper or post
twice.

On macOS it runs as a menu bar app (📄). Nothing pops up: a dot on the icon (📄•) means
today's reading is ready. Click the icon to see the paper and blog posts, click an item to
open it in your browser, and hover over it to see its summary.

## Setup

Requires [uv](https://docs.astral.sh/uv/). It installs Python 3.14 (pinned in
`.python-version`) and the dependencies from `uv.lock`.

```sh
uv sync
uv run dailypaper setup --interests "distributed systems, databases, ML infra" --api-key
uv run dailypaper app                  # run the menu bar app
uv run dailypaper install-login-item   # start it automatically at login
```

The login item runs the project's `.venv` Python directly, so it doesn't need uv on the
`PATH` at login. Run `uv run dailypaper install-login-item` again if you move the project
or recreate `.venv`.

The API key is stored in the macOS Keychain (`ANTHROPIC_API_KEY` also works). If you skip
`setup`, the app asks for the key and your interests on first launch.

## How it works

- **Once a day:** the app checks for today's paper at launch, when the Mac wakes, and every
  15 minutes, so a Mac left on overnight still gets a new paper. Only one paper is fetched
  per day unless you choose *Get another paper today*.
- **Picking:** `claude-haiku-5-5` uses web search to find a real paper and 3 blog posts, and
  submits them together through a strict `submit_reading` tool. Each item is checked before
  it's accepted: the paper must be an allowed type and not already suggested (matched by
  normalised title, DOI or arXiv ID); blog posts must not repeat earlier ones (matched by
  normalised URL or title); and every URL must resolve. Claude replaces any item that fails.
- **Paper types:** academic papers are switched off for now. To turn them back on, add
  `"academic"` to `ENABLED_PAPER_TYPES` in `dailypaper/core/config.py`.
- **Learning:** only papers are rated; blog posts aren't. Recent papers and their ratings go
  into each request. Every 7 ratings, Claude rewrites a short taste profile
  (`dailypaper profile`), so the prompt stays the same size however long you use the app.
  Blog posts are chosen from your interests and that profile. About 1 day in 5 the paper is
  an "exploration" pick from a nearby topic.

Data is stored in `~/Library/Application Support/DailyPaper/` (`papers.db`, `config.json`,
`app.log`).

## CLI

```
uv run dailypaper today | another | rate 4 [--id N] | history | profile | setup

today     show today's paper and blog posts, fetching them if needed
another   fetch an extra paper and 3 blog posts for today
```

## Building a .app

```sh
./scripts/build_app.sh   # -> dist/DailyPaper.app (py2app comes from the dev group)
```

Copy it to `/Applications`, open it, and turn on *Preferences → Start at login* so the
login item points at the bundle.

## Tests

```sh
uv run pytest
```
