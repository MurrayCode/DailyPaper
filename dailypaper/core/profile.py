"""A short written summary of the reader's taste, rebuilt from ratings every few ratings.

Sending this paragraph instead of the whole rating history keeps the daily prompt the
same size however long the app has been running.
"""

import anthropic

from .config import Config
from .storage import Paper

REFRESH_EVERY = 7
MAX_RATED = 150

PROFILE_PROMPT = """\
Below are papers a reader was recommended, each with their 1-5 star rating and topics.
Write a short taste profile (at most 120 words) that will guide future recommendations:
topics and kinds of paper they enjoy, what they dislike, preferred depth and length, and \
one or two adjacent areas worth trying. Be concrete and base it on the ratings. Reply \
with the profile text only.

Stated interests: {interests}

Previous profile (may be stale):
{previous}

Rated papers (newest first):
{rated}
"""


def needs_refresh(rating_count: int, rated_at_last_refresh: int) -> bool:
    return rating_count > 0 and rating_count - rated_at_last_refresh >= REFRESH_EVERY


def build_profile(client: anthropic.Anthropic, config: Config, previous: str, history: list[Paper]) -> str:
    rated = [p for p in history if p.stars][:MAX_RATED]
    lines = "\n".join(
        f"- [{p.stars}/5] {p.title} ({p.paper_type}, {p.year}; {', '.join(p.topics)})" for p in rated
    )
    response = client.messages.create(
        model=config.model,
        max_tokens=4000,
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": PROFILE_PROMPT.format(
                interests=config.interests or "(none)", previous=previous or "(none)", rated=lines
            ),
        }],
    )
    if response.stop_reason == "refusal":
        return previous
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or previous
