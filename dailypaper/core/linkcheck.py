"""Cheap check that a suggested URL points at something real."""

import urllib.error
import urllib.request

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) DailyPaper/0.1"
# Publishers often refuse scripted requests to pages that do exist (403, 429, ...).
# Only "gone" statuses count as a broken link.
_DEAD_STATUSES = {404, 410}


def url_looks_alive(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    if not url.startswith(("http://", "https://")):
        return False, "URL must start with http:// or https://"
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(request, timeout=timeout):
                return True, "ok"
        except urllib.error.HTTPError as exc:
            if exc.code in _DEAD_STATUSES:
                return False, f"HTTP {exc.code}"
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue  # some servers reject HEAD; retry with GET
            return True, f"HTTP {exc.code} (treated as reachable)"
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            return False, f"unreachable: {reason}"
    return True, "ok"
