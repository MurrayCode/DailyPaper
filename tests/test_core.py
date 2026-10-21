import random
from datetime import date
from types import SimpleNamespace

import pytest

from dailypaper.core import dedupe, profile
from dailypaper.core.config import Config
from dailypaper.core.recommender import RecommendationError, recommend
from dailypaper.core.service import DailyPaper
from dailypaper.core.storage import BlogPost, DuplicatePaper, Paper, Storage

OK = lambda paper, blogs: []  # noqa: E731 - validator that accepts everything


def make_paper(title="The Google File System", **overrides):
    data = {
        "title": title, "authors": ["Ghemawat", "Gobioff", "Leung"], "year": 2003, "source": "Google",
        "paper_type": "industry", "url": "https://research.google/pubs/the-google-file-system/", "doi": "",
        "arxiv_id": "", "topics": ["storage", "distributed systems"], "summary": "Describes GFS.",
        "why_recommended": "You like systems.", "reading_minutes": 45,
    }
    data.update(overrides)
    return data


def make_blogs(prefix="Post"):
    return [
        {"title": f"{prefix} {i}", "author": "A", "site": f"Site {i}",
         "url": f"https://site{i}.example/{prefix.lower()}-{i}", "summary": "One line."}
        for i in range(3)
    ]


def submission(paper=None, blogs=None, id="tu_1"):
    data = {"paper": paper or make_paper(), "blog_posts": make_blogs() if blogs is None else blogs}
    return SimpleNamespace(type="tool_use", name="submit_reading", input=data, id=id)


def response(*content, stop_reason="tool_use"):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


class FakeClient:
    def __init__(self, responses, profile_text="Likes systems papers."):
        self.responses = list(responses)
        self.calls = []
        self.profile_calls = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._profile)
        self.profile_text = profile_text

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)

    def _profile(self, **kwargs):
        self.profile_calls += 1
        return response(SimpleNamespace(type="text", text=self.profile_text), stop_reason="end_turn")


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.db")


# --- dedupe ------------------------------------------------------------------

def test_normalize_title_ignores_case_punctuation_and_accents():
    assert dedupe.normalize_title("Attention Is All You Need!") == dedupe.normalize_title("attention is  all you need")
    assert dedupe.normalize_title("Café: résumé") == "cafe resume"


def test_normalize_doi_and_arxiv():
    assert dedupe.normalize_doi("https://doi.org/10.1145/1234.5678") == "10.1145/1234.5678"
    assert dedupe.normalize_doi("") == ""
    assert dedupe.normalize_arxiv("", "https://arxiv.org/pdf/1706.03762v5") == "1706.03762"
    assert dedupe.normalize_arxiv("1706.03762v2") == "1706.03762"


def test_normalize_url_drops_scheme_www_tracking_and_slash():
    assert dedupe.normalize_url("https://www.Example.com/post/?utm_source=x#top") == "example.com/post"
    assert dedupe.normalize_url("http://example.com/?p=12") == "example.com?p=12"


# --- storage -----------------------------------------------------------------

def test_duplicates_rejected_by_title_doi_or_arxiv(storage):
    storage.add_paper(Paper(title="Paper One", url="https://x/1", doi="10.1234/abc"))
    assert storage.is_duplicate(Paper(title="paper one.", url="https://y"))
    assert storage.is_duplicate(Paper(title="Different", url="https://y", doi="https://doi.org/10.1234/ABC"))
    assert not storage.is_duplicate(Paper(title="Different", url="https://y"))
    with pytest.raises(DuplicatePaper):
        storage.add_paper(Paper(title="PAPER ONE", url="https://z"))


def test_arxiv_id_taken_from_url(storage):
    storage.add_paper(Paper(title="A", url="https://arxiv.org/abs/2401.00001v3"))
    assert storage.is_duplicate(Paper(title="B", url="https://example.com", arxiv_id="2401.00001"))


def test_reading_stored_together_and_blogs_deduped(storage):
    blogs = [BlogPost(title="Why Go", url="https://blog.example/why-go/", site="Ex")]
    paper, _ = storage.add_reading(Paper(title="A", url="https://x"), blogs)
    assert [b.title for b in storage.blogs_for(paper.id)] == ["Why Go"]
    assert storage.blog_is_duplicate(BlogPost(title="Other", url="http://www.blog.example/why-go"))
    assert storage.blog_is_duplicate(BlogPost(title="why go?", url="https://elsewhere"))
    # A repeated blog post rolls back the whole day, paper included.
    with pytest.raises(DuplicatePaper):
        storage.add_reading(Paper(title="B", url="https://y"), [BlogPost(title="X", url="https://blog.example/why-go")])
    assert not storage.is_duplicate(Paper(title="B", url="https://y"))


def test_rating_upserts_and_shows_in_history(storage):
    p = storage.add_paper(Paper(title="A", url="https://x"))
    storage.rate(p.id, 2)
    storage.rate(p.id, 5)
    assert storage.history()[0].stars == 5
    assert storage.rating_count() == 1
    with pytest.raises(ValueError):
        storage.rate(p.id, 6)


def test_due_once_per_day(storage):
    dp = DailyPaper(storage=storage, client=FakeClient([]), config=Config())
    day = date(2026, 10, 21)
    assert dp.is_due(day)
    storage.add_paper(Paper(title="A", url="https://x"), on=day)
    assert not dp.is_due(day)
    assert dp.is_due(date(2026, 10, 22))


# --- recommender -------------------------------------------------------------

def test_accepts_valid_submission_and_sends_expected_request():
    client = FakeClient([response(submission())])
    paper, blogs = recommend(client, Config(interests="Go"), "", [], [], validate=OK, rng=random.Random(1))
    assert paper.title == "The Google File System" and len(blogs) == 3
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert {t["name"] for t in call["tools"]} == {"web_search", "submit_reading"}
    assert "fallbacks" not in call  # Haiku 5.5 has no server-side fallback
    assert "tool_choice" not in call  # let the model think before submitting
    paper_schema = call["tools"][1]["input_schema"]["properties"]["paper"]
    assert paper_schema["properties"]["paper_type"]["enum"] == ["industry"]


def test_rejected_submission_is_sent_back_as_error_then_retried():
    client = FakeClient([
        response(submission(make_paper("Old Paper"), id="a")),
        response(submission(make_paper("New Paper"), id="b")),
    ])
    validate = lambda p, b: ["Paper: already suggested."] if p.title == "Old Paper" else []
    paper, _ = recommend(client, Config(), "", [], [], validate=validate)
    assert paper.title == "New Paper"
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and result["tool_use_id"] == "a"
    assert "already suggested" in result["content"]


def test_wrong_blog_count_or_repeats_rejected():
    blogs = make_blogs()
    client = FakeClient([
        response(submission(blogs=blogs[:2], id="a")),
        response(submission(blogs=[blogs[0], blogs[0], blogs[1]], id="b")),
        response(submission(id="c")),
    ])
    recommend(client, Config(), "", [], [], validate=OK)
    assert "exactly 3" in client.calls[1]["messages"][-1]["content"][0]["content"]
    assert "all be different" in client.calls[2]["messages"][-1]["content"][0]["content"]


def test_pause_turn_resumes_without_extra_user_message():
    search = SimpleNamespace(type="server_tool_use", name="web_search", input={}, id="s1")
    client = FakeClient([response(search, stop_reason="pause_turn"), response(submission())])
    recommend(client, Config(), "", [], [], validate=OK)
    second = client.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant"]


def test_refusal_raises():
    client = FakeClient([SimpleNamespace(stop_reason="refusal", content=[], stop_details=None)])
    with pytest.raises(RecommendationError):
        recommend(client, Config(), "", [], [], validate=OK)


def test_gives_up_after_repeated_rejections():
    client = FakeClient([response(submission(make_paper(f"P{i}"), id=str(i))) for i in range(5)])
    with pytest.raises(RecommendationError):
        recommend(client, Config(), "", [], [], validate=lambda p, b: ["nope."])


def test_prompt_includes_ratings_profile_and_past_blogs():
    history = [Paper(title="Loved It", url="u", stars=5, suggested_on="2026-10-20", year=2020, topics=["db"])]
    past_blogs = [BlogPost(title="Old Post", url="https://b", site="Blog")]
    client = FakeClient([response(submission())])
    recommend(client, Config(), "Likes databases.", history, past_blogs, validate=OK)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "[5/5] Loved It" in prompt and "Likes databases." in prompt
    assert "Old Post (Blog)" in prompt
    assert "Paper types allowed: industry only." in prompt


# --- config ------------------------------------------------------------------

def test_saved_academic_type_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILYPAPER_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"interests": "Go", "paper_types": ["academic", "industry"]}')
    assert Config.load().paper_types == ["industry"]
    (tmp_path / "config.json").write_text('{"paper_types": ["academic"]}')
    assert Config.load().paper_types == ["industry"]


# --- service -----------------------------------------------------------------

def test_service_validates_type_duplicates_and_urls(storage):
    storage.add_reading(Paper(title="Seen", url="https://x"), [BlogPost(title="Seen post", url="https://b/seen")])
    dp = DailyPaper(storage=storage, client=None, config=Config(),
                    check_url=lambda url: (url != "https://dead", "HTTP 404"))
    fresh = [BlogPost(title="New", url="https://b/new")]
    assert "only wants industry" in dp._validate(Paper(title="N", url="https://ok", paper_type="academic"), fresh)[0]
    assert "already suggested" in dp._validate(Paper(title="seen", url="https://ok", paper_type="industry"), fresh)[0]
    assert "doesn't work" in dp._validate(Paper(title="N", url="https://dead", paper_type="industry"), fresh)[0]
    good = Paper(title="N", url="https://ok", paper_type="industry")
    assert dp._validate(good, fresh) == []
    problems = dp._validate(good, [BlogPost(title="Seen post", url="https://b/seen"),
                                   BlogPost(title="Dead", url="https://dead")])
    assert len(problems) == 2


def test_ensure_today_fetches_once_and_stores_blogs(storage):
    client = FakeClient([response(submission())])
    dp = DailyPaper(storage=storage, client=client, config=Config(), check_url=lambda u: (True, "ok"))
    first, new1 = dp.ensure_today(date(2026, 10, 21))
    again, new2 = dp.ensure_today(date(2026, 10, 21))
    assert (new1, new2) == (True, False) and first.id == again.id
    assert len(client.calls) == 1
    assert len(dp.blogs_today(date(2026, 10, 21))) == 3


def test_profile_rebuilt_every_seven_ratings(storage):
    client = FakeClient([])
    dp = DailyPaper(storage=storage, client=client, config=Config())
    for i in range(profile.REFRESH_EVERY):
        p = storage.add_paper(Paper(title=f"P{i}", url="https://x"))
        dp.rate(p.id, 4)
    assert client.profile_calls == 1
    assert storage.get_meta("profile") == "Likes systems papers."
    p = storage.add_paper(Paper(title="extra", url="https://x"))
    dp.rate(p.id, 3)
    assert client.profile_calls == 1
