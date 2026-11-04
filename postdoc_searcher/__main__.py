"""Command line: python -m postdoc_searcher {run,plan,site,test-email}."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv

from . import fellowships, notify, site
from .config import DATA_DIR, DOCS_DIR, ROOT, load_config
from .pipeline import STEPS, RunOptions, due_search_tasks, run
from .store import State, Store
from .util import today


def _plan(cfg) -> None:
    s = cfg.settings
    ref = today()
    state = State(DATA_DIR / "state.json")
    print(f"Inputs: {len(cfg.pis)} PIs, {len(cfg.topics)} topics, {len(cfg.catalog.fellowships)} fellowships")
    print(f"AI: {'ANTHROPIC_API_KEY set' if os.environ.get('ANTHROPIC_API_KEY') else 'NO API KEY (keyword mode)'}"
          f" · model {s.llm.model} · budget ${s.llm.max_cost_per_run_usd:.2f}/run")
    print(f"Email: {'configured' if notify.smtp_configured() else 'NOT configured (SMTP_USER/SMTP_PASSWORD/EMAIL_TO)'}")
    missing = [p.name for p in cfg.pis if not p.country]
    if missing:
        print(f"⚠ PIs without country (no fellowship matching): {', '.join(missing)}")
    if not cfg.me.phd_defense_date:
        print("⚠ inputs/me.yaml: phd_defense_date is empty (years-since-PhD rules can't be checked)")
    if not cfg.me.nationality:
        print("⚠ inputs/me.yaml: nationality is empty (nationality rules can't be checked)")

    tasks = due_search_tasks(cfg, state, ref)
    cap = s.llm.max_search_tasks_per_run
    print(f"\nWeb-search tasks due: {len(tasks)} (next run does {min(cap, len(tasks))}):")
    for i, (_, key, _) in enumerate(tasks):
        print(f"  {'→' if i < cap else ' '} {key}")

    print("\nFellowships × your PIs:")
    for o in fellowships.build(cfg, state.fellowships, ref, {}):
        hosts = ", ".join(o.matched_pis) or "—"
        if o.rolling:
            when = "rolling"
        elif not (o.call_opens or o.deadline):
            when = "dates: web check pending"
        else:
            when = f"{'≈' if o.dates_estimated else ''}{o.call_opens or '?'} → {o.deadline or '?'}"
        print(f"  {o.title[:60]:60s} {when:26s} hosts: {hosts}")
        for pi, note in o.pi_notes.items():
            if note.startswith(("✗", "?")):
                print(f"      {pi}: {note[:110]}")

    # Rough per-call costs measured for Opus-class pricing; scaled for cheaper models.
    scale = {"claude-sonnet-5-5": 0.5, "claude-haiku-5-5": 0.03}.get(s.llm.model, 1.0)
    est = scale * (0.05 * 3 + 0.40 * min(cap, len(tasks)) + 0.30 * s.llm.max_fellowship_checks_per_run)
    print(f"\nRough AI cost of the next run with {s.llm.model}: ~${est:.2f} "
          f"(hard cap ${s.llm.max_cost_per_run_usd:.2f}; real costs are logged per run).")


def _test_email(cfg) -> int:
    if not notify.smtp_configured():
        print("Set SMTP_USER, SMTP_PASSWORD and EMAIL_TO (and optionally SMTP_HOST/SMTP_PORT) first.")
        return 1
    store = Store(DATA_DIR / "opportunities.json")
    items = sorted(store.all(), key=lambda o: -o.relevance)[:3]
    events = [("new", o) for o in items]
    msg = notify.compose(events, [], cfg.settings, today())
    msg.replace_header("Subject", f"{cfg.settings.email.subject_prefix} test email")
    notify.send(msg)
    print(f"Test email sent to {os.environ['EMAIL_TO']} with {len(events)} sample items.")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(prog="python -m postdoc_searcher", description="Postdoc Radar")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the daily search")
    r.add_argument("--no-ai", action="store_true", help="keyword scoring only, no Claude calls")
    r.add_argument("--no-web-search", action="store_true", help="skip the Claude web-search tasks")
    r.add_argument("--no-email", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="don't save anything or send email")
    r.add_argument("--max-tasks", type=int, help="override max_search_tasks_per_run")
    r.add_argument("--only", help=f"comma-separated subset of: {','.join(STEPS)}")
    sub.add_parser("plan", help="validate inputs and show what the next run would do")
    sub.add_parser("site", help="rebuild docs/data.js from the stored data")
    sub.add_parser("test-email", help="send a test email with the top stored items")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = load_config()
    if args.cmd == "plan":
        _plan(cfg)
        return 0
    if args.cmd == "site":
        site.build(cfg, Store(DATA_DIR / "opportunities.json"), State(DATA_DIR / "state.json"), DOCS_DIR)
        print(f"wrote {DOCS_DIR / 'data.js'}")
        return 0
    if args.cmd == "test-email":
        return _test_email(cfg)

    only = None
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}
        bad = only - set(STEPS)
        if bad:
            p.error(f"unknown step(s): {', '.join(sorted(bad))}")
    stats = run(cfg, RunOptions(use_ai=not args.no_ai, web_search=not args.no_web_search,
                                email=not args.no_email, dry_run=args.dry_run,
                                max_tasks=args.max_tasks, only=only))
    summary = {k: v for k, v in stats.items() if k not in ("errors",)}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    if stats["errors"]:
        print(f"{len(stats['errors'])} warnings/errors (see log above)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
