"""Watch each PI's group / 'join us' page and flag changes that mention openings."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import PI
from ..http import Http
from ..prefilter import OPENING_RE
from ..util import norm, now_iso, page_text, short_hash

log = logging.getLogger(__name__)


@dataclass
class PageChange:
    pi: PI
    url: str
    text: str
    first_check: bool
    mentions_opening: bool
    snippet: str


def _snippet(text: str, width: int = 300) -> str:
    m = OPENING_RE.search(text)
    if not m:
        return text[:width]
    start = max(0, m.start() - width // 2)
    return text[start:start + width]


def check(http: Http, pis: list[PI], state: dict) -> list[PageChange]:
    """Return pages whose visible text changed since the last run.

    `state` maps url → {"hash", "checked"} and is updated in place.
    """
    changes = []
    for pi in pis:
        url = pi.watch_url
        if not url:
            continue
        html = http.text(url)
        if not html:
            continue
        text = page_text(html)
        digest = short_hash(norm(text), 20)
        prev = state.get(url)
        state[url] = {"hash": digest, "checked": now_iso(), "pi": pi.name}
        if prev and prev.get("hash") == digest:
            continue
        mentions = bool(OPENING_RE.search(text))
        log.info("lab page %s for %s (%s)%s", "new" if not prev else "changed", pi.name, url,
                 " — mentions openings" if mentions else "")
        changes.append(PageChange(pi=pi, url=url, text=text, first_check=prev is None,
                                  mentions_opening=mentions, snippet=_snippet(text)))
    return changes
