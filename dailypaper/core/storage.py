import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import dedupe
from .paths import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY,
    suggested_on    TEXT NOT NULL,          -- local date, YYYY-MM-DD
    title           TEXT NOT NULL,
    norm_title      TEXT NOT NULL UNIQUE,
    doi             TEXT NOT NULL DEFAULT '',
    arxiv_id        TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL,
    authors         TEXT NOT NULL DEFAULT '[]',
    year            INTEGER,
    source          TEXT NOT NULL DEFAULT '',
    paper_type      TEXT NOT NULL DEFAULT '',
    topics          TEXT NOT NULL DEFAULT '[]',
    summary         TEXT NOT NULL DEFAULT '',
    why             TEXT NOT NULL DEFAULT '',
    reading_minutes INTEGER,
    opened_at       TEXT,
    created_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS papers_doi ON papers(doi) WHERE doi != '';
CREATE UNIQUE INDEX IF NOT EXISTS papers_arxiv ON papers(arxiv_id) WHERE arxiv_id != '';
CREATE INDEX IF NOT EXISTS papers_day ON papers(suggested_on);

CREATE TABLE IF NOT EXISTS ratings (
    paper_id  INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    stars     INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    rated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blog_posts (
    id            INTEGER PRIMARY KEY,
    paper_id      INTEGER REFERENCES papers(id) ON DELETE CASCADE,  -- suggested alongside this paper
    suggested_on  TEXT NOT NULL,
    title         TEXT NOT NULL,
    norm_title    TEXT NOT NULL,
    url           TEXT NOT NULL,
    norm_url      TEXT NOT NULL UNIQUE,
    author        TEXT NOT NULL DEFAULT '',
    site          TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    opened_at     TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS blog_posts_paper ON blog_posts(paper_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Paper:
    title: str
    url: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    source: str = ""
    paper_type: str = ""
    topics: list[str] = field(default_factory=list)
    summary: str = ""
    why: str = ""
    reading_minutes: int | None = None
    doi: str = ""
    arxiv_id: str = ""
    id: int | None = None
    suggested_on: str = ""
    opened_at: str | None = None
    stars: int | None = None

    @property
    def keys(self) -> tuple[str, str, str]:
        return (
            dedupe.normalize_title(self.title),
            dedupe.normalize_doi(self.doi),
            dedupe.normalize_arxiv(self.arxiv_id, self.url),
        )


@dataclass
class BlogPost:
    title: str
    url: str
    author: str = ""
    site: str = ""
    summary: str = ""
    id: int | None = None
    paper_id: int | None = None
    suggested_on: str = ""
    opened_at: str | None = None

    @property
    def keys(self) -> tuple[str, str]:
        return dedupe.normalize_title(self.title), dedupe.normalize_url(self.url)


class DuplicatePaper(Exception):
    pass


class Storage:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "papers.db"
        # One connection per thread: background jobs in the menu bar app open their own.
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # --- papers -------------------------------------------------------------

    def is_duplicate(self, paper: Paper) -> bool:
        title, doi, arxiv = paper.keys
        row = self.conn.execute(
            "SELECT 1 FROM papers WHERE norm_title = ?"
            " OR (? != '' AND doi = ?) OR (? != '' AND arxiv_id = ?) LIMIT 1",
            (title, doi, doi, arxiv, arxiv),
        ).fetchone()
        return row is not None

    def add_paper(self, paper: Paper, on: date | None = None) -> Paper:
        return self.add_reading(paper, [], on)[0]

    def add_reading(self, paper: Paper, blogs: list[BlogPost], on: date | None = None
                    ) -> tuple[Paper, list[BlogPost]]:
        """Store a day's paper and its blog posts together (all or nothing)."""
        title, doi, arxiv = paper.keys
        on_str = (on or date.today()).isoformat()
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO papers (suggested_on, title, norm_title, doi, arxiv_id, url, authors,"
                    " year, source, paper_type, topics, summary, why, reading_minutes, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        on_str, paper.title, title, doi, arxiv, paper.url, json.dumps(paper.authors),
                        paper.year, paper.source, paper.paper_type, json.dumps(paper.topics),
                        paper.summary, paper.why, paper.reading_minutes, _now(),
                    ),
                )
                paper.id = cur.lastrowid
                for blog in blogs:
                    norm_title, norm_url = blog.keys
                    blog.id = self.conn.execute(
                        "INSERT INTO blog_posts (paper_id, suggested_on, title, norm_title, url, norm_url,"
                        " author, site, summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (paper.id, on_str, blog.title, norm_title, blog.url, norm_url,
                         blog.author, blog.site, blog.summary, _now()),
                    ).lastrowid
                    blog.paper_id, blog.suggested_on = paper.id, on_str
        except sqlite3.IntegrityError as exc:
            paper.id = None
            raise DuplicatePaper(paper.title) from exc
        paper.suggested_on, paper.doi, paper.arxiv_id = on_str, doi, arxiv
        return paper, blogs

    def latest_for(self, on: date) -> Paper | None:
        rows = self._select("WHERE p.suggested_on = ? ORDER BY p.id DESC LIMIT 1", (on.isoformat(),))
        return rows[0] if rows else None

    def get(self, paper_id: int) -> Paper | None:
        rows = self._select("WHERE p.id = ?", (paper_id,))
        return rows[0] if rows else None

    def history(self, limit: int | None = None) -> list[Paper]:
        sql = "ORDER BY p.id DESC" + (" LIMIT ?" if limit else "")
        return self._select(sql, (limit,) if limit else ())

    def mark_opened(self, paper_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE papers SET opened_at = COALESCE(opened_at, ?) WHERE id = ?", (_now(), paper_id)
            )

    # --- blog posts ---------------------------------------------------------

    def blog_is_duplicate(self, blog: BlogPost) -> bool:
        norm_title, norm_url = blog.keys
        row = self.conn.execute(
            "SELECT 1 FROM blog_posts WHERE norm_url = ? OR norm_title = ? LIMIT 1", (norm_url, norm_title)
        ).fetchone()
        return row is not None

    def blogs_for(self, paper_id: int) -> list[BlogPost]:
        return self._select_blogs("WHERE paper_id = ? ORDER BY id", (paper_id,))

    def recent_blogs(self, limit: int) -> list[BlogPost]:
        return self._select_blogs("ORDER BY id DESC LIMIT ?", (limit,))

    def mark_blog_opened(self, blog_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE blog_posts SET opened_at = COALESCE(opened_at, ?) WHERE id = ?", (_now(), blog_id)
            )

    def _select_blogs(self, tail: str, params: tuple) -> list[BlogPost]:
        rows = self.conn.execute(
            "SELECT id, paper_id, suggested_on, title, url, author, site, summary, opened_at"
            " FROM blog_posts " + tail, params
        ).fetchall()
        return [BlogPost(**dict(r)) for r in rows]

    # --- ratings ------------------------------------------------------------

    def rate(self, paper_id: int, stars: int) -> None:
        if not 1 <= stars <= 5:
            raise ValueError("stars must be 1-5")
        with self.conn:
            self.conn.execute(
                "INSERT INTO ratings (paper_id, stars, rated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(paper_id) DO UPDATE SET stars = excluded.stars, rated_at = excluded.rated_at",
                (paper_id, stars, _now()),
            )

    def rating_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0]

    # --- meta (preference profile etc.) -------------------------------------

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- internals ----------------------------------------------------------

    def _select(self, tail: str, params: tuple) -> list[Paper]:
        rows = self.conn.execute(
            "SELECT p.*, r.stars FROM papers p LEFT JOIN ratings r ON r.paper_id = p.id " + tail, params
        ).fetchall()
        return [_row_to_paper(r) for r in rows]


def _row_to_paper(row: sqlite3.Row) -> Paper:
    return Paper(
        id=row["id"],
        suggested_on=row["suggested_on"],
        title=row["title"],
        url=row["url"],
        authors=json.loads(row["authors"]),
        year=row["year"],
        source=row["source"],
        paper_type=row["paper_type"],
        topics=json.loads(row["topics"]),
        summary=row["summary"],
        why=row["why"],
        reading_minutes=row["reading_minutes"],
        doi=row["doi"],
        arxiv_id=row["arxiv_id"],
        opened_at=row["opened_at"],
        stars=row["stars"],
    )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
