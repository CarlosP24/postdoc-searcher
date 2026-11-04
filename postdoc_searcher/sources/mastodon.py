"""Mastodon hashtag timelines (public API; includes posts bridged from Bluesky)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..http import Http
from ..models import RawItem
from ..util import html_to_text, truncate

log = logging.getLogger(__name__)


def collect(http: Http, instance: str, hashtags: list[str], max_age_days: int) -> list[RawItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[RawItem] = []
    seen: set[str] = set()
    for tag in hashtags:
        posts = http.json(f"{instance.rstrip('/')}/api/v1/timelines/tag/{tag.lstrip('#')}?limit=40") or []
        n = 0
        for p in posts:
            url = p.get("url") or p.get("uri")
            if not url or url in seen:
                continue
            try:
                created = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                created = None
            if created and created < cutoff:
                continue
            text = html_to_text(p.get("content", ""))
            if not text:
                continue
            seen.add(url)
            # Prefer a link inside the post (the actual ad) over the post itself.
            card = p.get("card") or {}
            first_line = text.splitlines()[0]
            out.append(RawItem(
                source=f"Mastodon #{tag}",
                title=truncate(card.get("title") or first_line, 160),
                url=card.get("url") or url,
                text=f"{text}\n\n(Social post: {url})",
                published=p.get("created_at"),
                extra={"post_url": url},
            ))
            n += 1
        log.info("%-28s %3d posts", f"Mastodon #{tag}", n)
    return out
