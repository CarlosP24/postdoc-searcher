"""Cheap keyword pre-selection of raw items, and PI ↔ offer association."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import PI, Topic
from .models import RawItem
from .util import norm

POSTDOC_RE = re.compile(
    r"post[\s-]?doc|postdoctoral|post-doctoral|research (fellow|associate|scientist)|fellowship|"
    r"junior (researcher|group leader)|researcher (position|job)|wissenschaftlich|investigador|chercheu|"
    r"ricercat|onderzoeker|forskare|tutkija|marie curie|msca|vacanc|job offer|we are hiring",
    re.I,
)

# A title with one of these is never dropped by exclude_title_patterns.
POSTDOC_TITLE_RE = re.compile(
    r"post[\s-]?doc|postdoctoral|post-doctoral|research (fellow|associate)|fellowship", re.I)

OPENING_RE = re.compile(
    r"(open|available|vacant|funded)\s+(post[\s-]?doc\w*|positions?|fellowships?)|vacanc|"
    r"we are (hiring|looking for|recruiting|seeking)|(is|are) (hiring|recruiting)|positions? available|"
    r"join (us|the group|our (team|group))|call for applications|apply (now|by|before)|"
    r"application deadline|postdoc(toral)? (position|opening|opportunit)",
    re.I,
)


@dataclass
class Match:
    item: RawItem
    score: float
    topics: list[str]
    pis: list[str]


def keyword_regex(keyword: str) -> re.Pattern:
    # word-prefix match, accent-insensitive (text is normalised before matching)
    return re.compile(r"(?<![a-z0-9])" + re.escape(norm(keyword)))


class Prefilter:
    """Keyword pre-selection.

    broad=True (Claude available): ads matching only `field_keywords` also pass,
    with a small score, so Claude can judge vaguely-worded physics ads.
    """

    def __init__(self, topics: list[Topic], pis: list[PI], exclude_title_patterns: list[str],
                 field_keywords: list[str] | None = None, broad: bool = False):
        self.topics = [(t, [keyword_regex(k) for k in t.keywords]) for t in topics]
        self.pis = pis
        self.exclude = [re.compile(p, re.I) for p in exclude_title_patterns]
        self.field = [keyword_regex(k) for k in (field_keywords or [])]
        self.broad = broad

    def excluded(self, title: str) -> bool:
        t = norm(title)
        return any(p.search(t) for p in self.exclude) and not POSTDOC_TITLE_RE.search(t)

    def match_topics(self, text: str) -> list[tuple[Topic, int]]:
        t = norm(text)
        out = []
        for topic, regs in self.topics:
            hits = sum(1 for r in regs if r.search(t))
            if hits:
                out.append((topic, hits))
        return out

    def score(self, item: RawItem, broad: bool | None = None) -> Match | None:
        broad = self.broad if broad is None else broad
        if self.excluded(item.title):
            return None
        text = f"{item.title}\n{item.institution or ''}\n{item.text}"
        if not item.trusted and not POSTDOC_RE.search(norm(text)):
            return None
        topics = self.match_topics(text)
        pis = mentioned_pis(self.pis, text)
        score = sum(t.weight * (1 + 0.25 * (hits - 1)) for t, hits in topics) + 2.0 * len(pis)
        if not topics and not pis and not item.trusted:
            if not (broad and any(r.search(norm(text)) for r in self.field)):
                return None
            score = 0.1
        return Match(item=item, score=score, topics=[t.name for t, _ in topics], pis=pis)

    def heuristic_relevance(self, m: Match) -> int:
        """0–100 score when no AI is available."""
        return int(min(90, 30 + 25 * m.score))


def _name_parts(name: str) -> tuple[str, str]:
    parts = norm(name).replace(".", " ").split()
    return (parts[0] if parts else ""), (parts[-1] if parts else "")


def mentioned_pis(pis: list[PI], text: str) -> list[str]:
    t = norm(text)
    found = []
    for p in pis:
        first, last = _name_parts(p.name)
        if len(last) < 3:
            continue
        if norm(p.name) in t:
            found.append(p.name)
        elif re.search(rf"(?<![a-z]){re.escape(last)}(?![a-z])", t) and (
            re.search(rf"(?<![a-z]){re.escape(first)}(?![a-z])", t)
            or re.search(rf"(prof\.?|professor|dr\.?|group of|lab of)\s+{re.escape(last)}", t)
        ):
            found.append(p.name)
    return found


def same_person(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    fa, la = _name_parts(a)
    fb, lb = _name_parts(b)
    return bool(la) and la == lb and (fa[:1] == fb[:1])


_INSTITUTION_STOP = {
    "university", "universidad", "universitat", "universite", "universita", "universiteit",
    "institute", "instituto", "institut", "of", "the", "de", "and", "for", "la", "del", "der",
    "college", "school", "department", "centre", "center", "national", "research", "technology",
    "science", "sciences", "physics", "faculty", "group", "lab", "laboratory",
}


def institution_tokens(name: str | None) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", norm(name)) if len(w) >= 3 and w not in _INSTITUTION_STOP}


def same_institution(a: str | None, b: str | None) -> bool:
    ta, tb = institution_tokens(a), institution_tokens(b)
    if not ta or not tb:
        return False
    overlap = ta & tb
    return len(overlap) >= min(2, len(ta), len(tb))
