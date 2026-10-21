"""Identity keys, so the same paper or blog post is never suggested twice.

A paper matches an earlier one if any of its normalised title, DOI or arXiv ID match.
A blog post matches on normalised URL or title.
"""

import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_BARE_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?$")
_DOI_RE = re.compile(r"(10\.\d{4,9}/\S+)", re.I)


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def normalize_doi(doi: str) -> str:
    match = _DOI_RE.search(doi or "")
    return match.group(1).lower().rstrip(".") if match else ""


def normalize_arxiv(arxiv_id: str, url: str = "") -> str:
    for candidate in (arxiv_id or "", url or ""):
        candidate = candidate.strip()
        match = _BARE_ARXIV_RE.match(candidate) or _ARXIV_RE.search(candidate)
        if match:
            return match.group(1)
    return ""


def normalize_url(url: str) -> str:
    """Scheme, www., fragment, tracking params and trailing slash removed."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = "&".join(
        f"{k}={v}" for k, v in parse_qsl(parts.query) if not (k.lower().startswith("utm_") or k.lower() in ("ref", "source"))
    )
    path = parts.path.rstrip("/")
    return host + path + (f"?{query}" if query else "")
