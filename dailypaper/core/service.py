"""High-level operations shared by the CLI and the menu bar app."""

import logging
from datetime import date

import anthropic

from . import profile as profile_mod
from .config import Config, get_api_key
from .linkcheck import url_looks_alive
from .recommender import EXCLUDE_BLOGS, recommend
from .storage import BlogPost, DuplicatePaper, Paper, Storage

log = logging.getLogger(__name__)

META_PROFILE = "profile"
META_PROFILE_AT = "profile_rating_count"


def make_client() -> anthropic.Anthropic:
    key = get_api_key()
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


class DailyPaper:
    def __init__(self, storage: Storage | None = None, client: anthropic.Anthropic | None = None,
                 config: Config | None = None, check_url=url_looks_alive):
        self.storage = storage or Storage()
        self._client = client
        self.config = config or Config.load()
        self.check_url = check_url

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = make_client()
        return self._client

    def today(self, on: date | None = None) -> Paper | None:
        return self.storage.latest_for(on or date.today())

    def is_due(self, on: date | None = None) -> bool:
        return self.today(on) is None

    def blogs_today(self, on: date | None = None) -> list[BlogPost]:
        paper = self.today(on)
        return self.storage.blogs_for(paper.id) if paper else []

    def fetch(self, on: date | None = None) -> Paper:
        """Get a new paper and blog posts from Claude and store them (also used for "another one today")."""
        on = on or date.today()
        paper, blogs = recommend(
            self.client,
            self.config,
            profile=self.storage.get_meta(META_PROFILE),
            history=self.storage.history(),
            recent_blogs=self.storage.recent_blogs(EXCLUDE_BLOGS),
            validate=self._validate,
            today=on,
        )
        try:
            return self.storage.add_reading(paper, blogs, on=on)[0]
        except DuplicatePaper:
            # The validator already checked this, so this only happens if two fetches race.
            log.warning("Duplicate slipped past validation: %s", paper.title)
            raise

    def ensure_today(self, on: date | None = None) -> tuple[Paper, bool]:
        """Return today's paper, fetching one if needed. The bool is True if it's new."""
        existing = self.today(on)
        if existing:
            return existing, False
        return self.fetch(on), True

    def rate(self, paper_id: int, stars: int) -> None:
        self.storage.rate(paper_id, stars)
        count = self.storage.rating_count()
        last = int(self.storage.get_meta(META_PROFILE_AT, "0"))
        if profile_mod.needs_refresh(count, last):
            try:
                new = profile_mod.build_profile(
                    self.client, self.config, self.storage.get_meta(META_PROFILE), self.storage.history()
                )
            except anthropic.APIError:
                log.exception("Profile refresh failed; will retry after the next rating")
                return
            self.storage.set_meta(META_PROFILE, new)
            self.storage.set_meta(META_PROFILE_AT, str(count))

    def _validate(self, paper: Paper, blogs: list[BlogPost]) -> list[str]:
        problems = []
        if paper.paper_type not in self.config.paper_types:
            problems.append(f"Paper: the reader only wants {' or '.join(self.config.paper_types)} papers.")
        elif self.storage.is_duplicate(paper):
            problems.append(f'Paper: "{paper.title}" was already suggested on an earlier day.')
        else:
            problems += self._url_problem("Paper", paper.url)
        for blog in blogs:
            label = f'Blog post "{blog.title}"'
            if self.storage.blog_is_duplicate(blog):
                problems.append(f"{label} was already suggested on an earlier day.")
            else:
                problems += self._url_problem(label, blog.url)
        return problems

    def _url_problem(self, label: str, url: str) -> list[str]:
        alive, detail = self.check_url(url)
        if alive:
            return []
        return [f"{label}: the URL {url} doesn't work ({detail}). Use a URL you saw in search results."]
