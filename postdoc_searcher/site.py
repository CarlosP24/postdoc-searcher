"""Write the dashboard data (docs/data.js works from file:// and GitHub Pages)."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .store import State, Store
from .util import now_iso


def build(cfg: Config, store: Store, state: State, docs_dir: Path) -> None:
    payload = {
        "generated_at": now_iso(),
        "title": cfg.settings.dashboard.title,
        "notify_threshold": cfg.settings.relevance.notify_threshold,
        "opportunities": [o.model_dump() for o in store.all()],
        "pis": [
            {"name": p.name, "institution": p.institution, "country": p.country,
             "website": p.website, "priority": p.priority,
             "last_searched": state.tasks.get(f"pi:{p.name}")}
            for p in cfg.pis
        ],
        "topics": [{"name": t.name, "last_searched": state.tasks.get(f"topic:{t.name}")} for t in cfg.topics],
        "runs": state.runs[-60:],
    }
    docs_dir.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=1)
    (docs_dir / "data.json").write_text(blob + "\n", encoding="utf-8")
    (docs_dir / "data.js").write_text(f"window.POSTDOC_DATA = {blob};\n", encoding="utf-8")
