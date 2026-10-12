"""Resolve spoken web destinations and exact search queries into validated HTTP URLs."""

from __future__ import annotations

import re
from time import monotonic
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from Foundation import NSURL


WEB_OPERATIONS = {"OPEN_WEBSITE", "SEARCH_WEB"}
BROWSER_BUNDLES = {"com.apple.Safari", "com.google.Chrome", "org.mozilla.firefox", "com.microsoft.edgemac",
                   "com.brave.Browser", "company.thebrowser.Browser", "com.vivaldi.Vivaldi", "com.operasoftware.Opera"}
WEBSITES = {"youtube": "https://www.youtube.com/", "google": "https://www.google.com/",
            "github": "https://github.com/", "wikipedia": "https://www.wikipedia.org/",
            "reddit": "https://www.reddit.com/", "stripe": "https://stripe.com/",
            "gmail": "https://mail.google.com/"}
SEARCH_SITES = {"google": ("https://www.google.com/search", "q"),
                "youtube": ("https://www.youtube.com/results", "search_query")}


def suffix_search_span(goal: str) -> tuple[int, int, int] | None:
    """Propose the exact query in search/find-for-query-on-site commands, shielding literals."""

    quotes = list(re.finditer(r'"(?:\\.|[^"\\])*"|“[^”]*”|(?<!\w)\x27[^\x27]*\x27(?!\w)', goal))
    masked = re.sub(r'"(?:\\.|[^"\\])*"|“[^”]*”|(?<!\w)\x27[^\x27]*\x27(?!\w)', lambda match: ' ' * len(match.group()), goal)
    search = next((match for match in re.finditer(r'\b(?:search|find|locate)\s+(?:me\s+)?(?:for\s+)?', goal, re.I)
                   if not any(quote.start() <= match.start() < quote.end() for quote in quotes)), None)
    typing = re.search(r'\b(?:type|write|dictate|insert)\s+', masked, re.I)
    if search is None or typing and typing.start() < search.start():
        return None
    if re.match(r'(?:in\s+)?Finder\b', goal[search.end():], re.I):
        return None
    site = re.search(r'(?<=\s)(?:on|in)\s+(?:youtube|google)(?=\s+(?:in|using|with)\s+[^.!?]+[.!?]?$|\s*[.!?]?$)', masked[search.end():], re.I)
    if site is None:
        return None
    start, end = search.end(), search.end() + site.start()
    while start < end and goal[start].isspace():
        start += 1
    while end > start and goal[end - 1].isspace():
        end -= 1
    quoted = next((quote for quote in quotes if quote.start() == start and quote.end() == end), None)
    if quoted:
        start, end = start + 1, end - 1
    return (search.start(), start, end) if start < end else None


def navigation_spans(goal: str) -> list[tuple[int, int]]:
    """Offer destination spans only after navigation verbs outside quoted literals."""

    quotes = list(re.finditer(r'"(?:\\.|[^"\\])*"|“[^”]*”|(?<!\w)\x27[^\x27]*\x27(?!\w)', goal))
    spans = []
    for match in re.finditer(r"\b(?:go\s+to|navigate\s+to|visit|open)\s+", goal, re.IGNORECASE):
        if any(item.start() <= match.start() < item.end() for item in quotes):
            continue
        quoted = next((item for item in quotes if item.start() == match.end()), None)
        if quoted:
            spans.append((quoted.start() + 1, quoted.end() - 1))
            continue
        start = match.end()
        spans.append((start, len(goal)))
        for boundary in re.finditer(r"\s+(?:(?:in|using|with)\s+|and\s+(?:then\s+)?(?:open|switch|type|write|search|go)\b)", goal[start:], re.IGNORECASE):
            spans.append((start, start + boundary.start()))
    return spans


def normalize_web_url(text: str) -> str | None:
    """Accept named sites or explicit domains, never credentials or non-web schemes."""

    if not isinstance(text, str) or len(text) > 4096 or any(ord(c) < 32 for c in text):
        return None
    text = text.strip()
    name = text.casefold().rstrip(".!?")
    if name in WEBSITES:
        return WEBSITES[name]
    text = re.sub(r"\s+dot\s+", ".", text, flags=re.IGNORECASE)
    if not text or any(c.isspace() or c in "\\\"<>" for c in text):
        return None
    if "://" not in text:
        if "/" not in text and "?" not in text and "#" not in text:
            text = text.rstrip(".")
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port
        if parts.scheme.lower() not in {"http", "https"} or parts.username is not None or parts.password is not None:
            return None
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}", host):
            return None
        if port is not None and not 1 <= port <= 65535:
            return None
    except (ValueError, UnicodeError):
        return None
    authority = host + (f":{port}" if port is not None else "")
    return urlunsplit((parts.scheme.lower(), authority, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                       quote(parts.query, safe="%=&?/:@!$'()*+,;~-._"), quote(parts.fragment, safe="%/?@:!$&'()*+,;=~-._")))


def search_url(site: str, query: str) -> str | None:
    """Encode a bounded literal query without allowing it to change the destination."""

    if site not in SEARCH_SITES or not isinstance(query, str) or not query.strip() or len(query) > 4096:
        return None
    if any(ord(c) < 32 or c in "\u2028\u2029" for c in query):
        return None
    base, parameter = SEARCH_SITES[site]
    return base + "?" + urlencode({parameter: query})


def browser_url(text, window) -> str | None:
    """Read only window/web-area URLs, never arbitrary links or existing page text."""

    direct = text.read(window, "AXURL") or text.read(window, "AXDocument")
    direct = str(direct.absoluteString()) if isinstance(direct, NSURL) else direct
    if isinstance(direct, str) and normalize_web_url(direct):
        return normalize_web_url(direct)
    pending, found, seen = [window], set(), []
    deadline = monotonic() + 1
    while pending and len(seen) < 128 and monotonic() < deadline:
        node = pending.pop()
        if any(text.equal(node, old) for old in seen):
            continue
        seen.append(node)
        role = text.read(node, "AXRole")
        if role == "AXWebArea":
            url = text.read(node, "AXURL")
            url = str(url.absoluteString()) if isinstance(url, NSURL) else url
            normalized = normalize_web_url(url) if isinstance(url, str) else None
            if normalized:
                found.add(normalized)
            continue
        if role in {"AXTable", "AXList", "AXOutline"}:
            continue
        children = text.read(node, "AXVisibleChildren") or text.read(node, "AXChildren") or []
        pending.extend(list(children)[:max(0, 128 - len(pending))])
    return next(iter(found)) if len(found) == 1 else None


def web_url_matches(expected: str, observed: str | None, *, search: bool = False) -> bool:
    """Allow extra search tracking parameters while retaining the exact query and site."""

    if not observed:
        return False
    expected, observed = normalize_web_url(expected), normalize_web_url(observed)
    if not expected or not observed:
        return False
    if not search:
        return expected == observed
    wanted, actual = urlsplit(expected), urlsplit(observed)
    return (wanted.scheme, wanted.netloc, wanted.path, wanted.fragment) == (actual.scheme, actual.netloc, actual.path, actual.fragment) and all(
        parse_qs(actual.query, keep_blank_values=True).get(key) == values
        for key, values in parse_qs(wanted.query, keep_blank_values=True).items())
