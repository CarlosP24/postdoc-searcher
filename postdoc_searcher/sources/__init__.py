"""Free (non-AI) sources of postdoc offers."""

from __future__ import annotations

import logging

from ..config import Config
from ..http import Http
from ..models import RawItem
from . import feeds, inspire, jobs_ac_uk, mastodon

log = logging.getLogger(__name__)


def board_queries(cfg: Config) -> list[str]:
    seen, out = set(), []
    for t in cfg.topics:
        for q in t.board_queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
    return out


def collect_all(cfg: Config, http: Http) -> list[RawItem]:
    s = cfg.settings.sources
    queries = board_queries(cfg)
    items: list[RawItem] = []
    steps = [
        ("keyword feeds", lambda: feeds.collect_keyword_feeds(http, s.keyword_feeds, queries)),
        ("feeds", lambda: feeds.collect_feeds(http, s.feeds)),
        ("jobs.ac.uk", lambda: jobs_ac_uk.collect(http, queries) if s.jobs_ac_uk.enabled else []),
        ("inspire", lambda: inspire.collect(http, s.inspire.query) if s.inspire.enabled else []),
        ("mastodon", lambda: mastodon.collect(http, s.mastodon.instance, s.mastodon.hashtags,
                                              s.mastodon.max_age_days) if s.mastodon.enabled else []),
    ]
    for name, fn in steps:
        try:
            items.extend(fn())
        except Exception as exc:  # one broken source must not stop the run
            log.exception("source %s failed", name)
            http.errors.append(f"{name}: {exc}")
    # de-duplicate by canonical URL
    unique: dict[str, RawItem] = {}
    for it in items:
        unique.setdefault(it.key, it)
    log.info("collected %d raw items (%d unique)", len(items), len(unique))
    return list(unique.values())
