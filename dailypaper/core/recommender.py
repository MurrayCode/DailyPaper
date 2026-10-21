"""Asks Claude to pick today's reading: one paper plus a few blog posts.

Claude gets the server-side web search tool (so it suggests real, findable pieces rather
than recalling citations from memory) and a strict `submit_reading` client tool for its
answer. Each submission is checked locally (allowed type, not a repeat, URLs resolve); a
rejected submission goes back to Claude as an error tool_result so it can replace the
items that failed.
"""

import random
from collections.abc import Callable
from datetime import date

import anthropic

from .config import ENABLED_PAPER_TYPES, Config
from .storage import BlogPost, Paper

MAX_REQUESTS = 12          # total API round trips (searches, pause_turn resumes, retries)
MAX_REJECTIONS = 3         # submissions we bounce back before giving up
EXPLORATION_RATE = 0.2
RECENT_HISTORY = 30        # rated history shown in full
EXCLUDE_TITLES = 400       # titles listed as "already suggested" (the DB is the real guard)
BLOG_COUNT = 3
EXCLUDE_BLOGS = 90

SYSTEM_PROMPT = f"""\
Each day you choose reading for a single reader: one paper, plus {BLOG_COUNT} blog posts \
on the same interests.

The paper should be worth an hour of a thoughtful person's attention: influential, \
unusually clear, or surprisingly useful for this reader. Paper types, as the reader's \
settings allow:
- academic: peer-reviewed papers, conference papers, or well-regarded preprints (e.g. arXiv).
- industry: technical whitepapers and engineering papers from companies, standards bodies, \
or research labs (e.g. the Google File System paper, the Bitcoin whitepaper, a cloud \
provider's architecture whitepaper, an AI lab's technical report). Marketing material \
doesn't count.

The blog posts are shorter reads (5-20 minutes): substantive posts by practitioners or \
engineering teams, such as deep dives, lessons learned, or clear explanations. Not news \
articles, press releases, or listicles. Pick {BLOG_COUNT} posts from different sites; they \
can complement the paper but don't have to be about it.

How to work:
1. Use web search to find candidates and to confirm each choice exists. Every URL you give \
must be one you saw in search results: the canonical page or PDF, preferably free to read. \
Never construct or guess a URL.
2. Never pick anything in the reader's already-suggested lists, including other versions \
or editions of the same paper.
3. Use the reader's paper ratings: lean towards what they rated 4-5 and away from what they \
rated 1-2. Unrated papers carry no signal. Blog posts aren't rated; choose them from the \
reader's interests and taste profile.
4. Call the submit_reading tool exactly once with your final choices. If the tool rejects \
some items, replace only those and call it again with the full set.

Summaries appear in the reader's menu bar. Write them in plain language. The paper's \
summary is about three sentences on what it does and why it matters, and \
`why_recommended` is one sentence tying it to their interests or ratings. Each blog \
post's summary is one sentence.\
"""

_PAPER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "authors": {"type": "array", "items": {"type": "string"},
                    "description": "Authors, or the publishing organisation for industry whitepapers."},
        "year": {"type": "integer"},
        "source": {"type": "string", "description": "Venue, journal, or publishing organisation."},
        "paper_type": {"type": "string", "enum": list(ENABLED_PAPER_TYPES)},
        "url": {"type": "string", "description": "Landing page or PDF URL seen in search results."},
        "doi": {"type": "string", "description": "DOI if known, else empty string."},
        "arxiv_id": {"type": "string", "description": "arXiv ID like 1706.03762 if any, else empty string."},
        "topics": {"type": "array", "items": {"type": "string"}, "description": "2-5 short topic tags."},
        "summary": {"type": "string"},
        "why_recommended": {"type": "string"},
        "reading_minutes": {"type": "integer"},
    },
    "required": ["title", "authors", "year", "source", "paper_type", "url", "doi", "arxiv_id",
                 "topics", "summary", "why_recommended", "reading_minutes"],
    "additionalProperties": False,
}

_BLOG_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "author": {"type": "string", "description": "Author or team name, else empty string."},
        "site": {"type": "string", "description": "Blog or publication name."},
        "url": {"type": "string", "description": "Post URL seen in search results."},
        "summary": {"type": "string"},
    },
    "required": ["title", "author", "site", "url", "summary"],
    "additionalProperties": False,
}

SUBMIT_TOOL = {
    "name": "submit_reading",
    "description": f"Submit today's reading: one paper and exactly {BLOG_COUNT} blog posts.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "paper": _PAPER_SCHEMA,
            "blog_posts": {"type": "array", "items": _BLOG_SCHEMA,
                           "description": f"Exactly {BLOG_COUNT} posts from different sites."},
        },
        "required": ["paper", "blog_posts"],
        "additionalProperties": False,
    },
}

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 12}


class RecommendationError(Exception):
    pass


# Returns a list of problems (empty if the submission is acceptable).
Validator = Callable[[Paper, list[BlogPost]], list[str]]


def build_user_prompt(
    config: Config,
    profile: str,
    history: list[Paper],
    recent_blogs: list[BlogPost],
    today: date,
    explore: bool,
) -> str:
    types = " and ".join(config.paper_types)
    parts = [
        f"Today is {today.isoformat()}.",
        f"Paper types allowed: {types} only.",
        f"Reader's stated interests:\n{config.interests.strip() or '(none given; pick broadly in computing and science)'}",
    ]
    if profile:
        parts.append(f"What their ratings so far suggest about their taste:\n{profile}")

    recent = history[:RECENT_HISTORY]
    if recent:
        lines = []
        for p in recent:
            stars = f"{p.stars}/5" if p.stars else "unrated"
            lines.append(f"- {p.suggested_on} [{stars}] {p.title} ({p.year}; {', '.join(p.topics)})")
        parts.append("Recent papers and ratings (newest first):\n" + "\n".join(lines))

    older = history[RECENT_HISTORY:EXCLUDE_TITLES]
    if older:
        parts.append("Also already suggested (do not repeat):\n" + "\n".join(f"- {p.title}" for p in older))

    if recent_blogs:
        parts.append("Blog posts already suggested (do not repeat):\n"
                     + "\n".join(f"- {b.title} ({b.site})" for b in recent_blogs))

    if explore:
        parts.append(
            "Today is an exploration day for the paper: pick something adjacent to, but outside, "
            "the topics they usually see, which they'd still plausibly enjoy."
        )
    parts.append(f"Find today's paper and {BLOG_COUNT} blog posts, and submit them with submit_reading.")
    return "\n\n".join(parts)


def recommend(
    client: anthropic.Anthropic,
    config: Config,
    profile: str,
    history: list[Paper],
    recent_blogs: list[BlogPost],
    validate: Validator,
    today: date | None = None,
    rng: random.Random | None = None,
) -> tuple[Paper, list[BlogPost]]:
    today = today or date.today()
    explore = (rng or random).random() < EXPLORATION_RATE
    messages: list[dict] = [
        {"role": "user", "content": build_user_prompt(config, profile, history, recent_blogs, today, explore)}
    ]
    rejections = 0

    for _ in range(MAX_REQUESTS):
        response = client.beta.messages.create(
            model=config.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL, SUBMIT_TOOL],
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            messages=messages,
        )

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RecommendationError(f"Claude declined the request ({getattr(details, 'category', None)})")

        _append_assistant(messages, response.content)

        if response.stop_reason == "pause_turn":
            continue  # server-side search loop paused; resend as-is and it resumes

        submissions = [b for b in response.content if b.type == "tool_use" and b.name == SUBMIT_TOOL["name"]]
        if not submissions:
            if response.stop_reason == "max_tokens":
                raise RecommendationError("Response hit max_tokens before a recommendation was made")
            messages.append({"role": "user", "content": "Please submit your choices with the submit_reading tool."})
            continue

        results, accepted = [], None
        for block in submissions:
            if accepted is not None:
                results.append(_tool_result(block.id, "Ignored: a submission was already accepted."))
                continue
            paper, blogs = _paper_from_input(block.input["paper"]), [
                _blog_from_input(b) for b in block.input["blog_posts"]
            ]
            problems = _check_shape(blogs) + validate(paper, blogs)
            if problems:
                text = "Rejected:\n" + "\n".join(f"- {p}" for p in problems) + "\nReplace these and submit again."
                results.append(_tool_result(block.id, text, error=True))
            else:
                accepted = (paper, blogs)
                results.append(_tool_result(block.id, "Accepted."))
        if accepted:
            return accepted

        rejections += 1
        if rejections >= MAX_REJECTIONS:
            raise RecommendationError(f"Gave up after {rejections} rejected submissions")
        messages.append({"role": "user", "content": results})

    raise RecommendationError("No recommendation after the maximum number of requests")


def _check_shape(blogs: list[BlogPost]) -> list[str]:
    problems = []
    if len(blogs) != BLOG_COUNT:
        problems.append(f"Send exactly {BLOG_COUNT} blog posts (got {len(blogs)}).")
    if len({b.keys[1] for b in blogs}) != len(blogs):
        problems.append("The blog posts must all be different posts.")
    return problems


def _append_assistant(messages: list[dict], content: list) -> None:
    # After a pause_turn the next response continues the same assistant turn.
    if messages and messages[-1]["role"] == "assistant":
        messages[-1]["content"] = [*messages[-1]["content"], *content]
    else:
        messages.append({"role": "assistant", "content": list(content)})


def _tool_result(tool_use_id: str, text: str, error: bool = False) -> dict:
    result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
    if error:
        result["is_error"] = True
    return result


def _paper_from_input(data: dict) -> Paper:
    return Paper(
        title=data["title"].strip(),
        url=data["url"].strip(),
        authors=data["authors"],
        year=data["year"],
        source=data["source"],
        paper_type=data["paper_type"],
        topics=data["topics"],
        summary=data["summary"].strip(),
        why=data["why_recommended"].strip(),
        reading_minutes=data["reading_minutes"],
        doi=data["doi"],
        arxiv_id=data["arxiv_id"],
    )


def _blog_from_input(data: dict) -> BlogPost:
    return BlogPost(
        title=data["title"].strip(),
        url=data["url"].strip(),
        author=data["author"].strip(),
        site=data["site"].strip(),
        summary=data["summary"].strip(),
    )
