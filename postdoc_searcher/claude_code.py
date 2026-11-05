"""AI backend that runs Claude Code headlessly (`claude -p`), using your Claude subscription.

Same interface as `llm.Claude` (triage, analyze_page, search_pi, search_topic,
fellowship_status, usage, refusals, check_budget), so the pipeline doesn't care
which backend is active.

Isolation: every call runs in an empty temporary directory with `--safe-mode`
(no CLAUDE.md, skills, plugins, hooks or MCP servers), a custom system prompt,
only the tools it needs, and no Anthropic API key in its environment — so it
can only bill your subscription, never API credits.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import PI, Config, Fellowship, Topic
from .llm import (FINISH_JSON, BudgetExceeded, Usage, build_system_prompt, fellowship_task, page_prompt,
                  pi_task, tool_schema, topic_task, triage_prompt)
from .models import CallStatus, PageAnalysis, RawItem, SearchReport, TriageBatch, TriageDecision

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
_LIMIT_WORDS = ("usage limit", "rate limit", "limit reached", "out of extra usage", "credit balance")
_AUTH_WORDS = ("not logged in", "/login", "invalid api key", "oauth token", "authentication", "unauthorized")


def _stop_words(text: str) -> str | None:
    """A reason to stop AI for the rest of the run (plan limit or missing login), or None."""
    t = text.lower()
    if any(w in t for w in _LIMIT_WORDS):
        return "Claude subscription limit reached"
    if any(w in t for w in _AUTH_WORDS):
        return "Claude Code is not logged in (run `claude` once, or set CLAUDE_CODE_OAUTH_TOKEN)"
    return None


def find_claude(configured: str | None = None) -> str | None:
    """Locate the `claude` CLI: settings → $CLAUDE_BIN → PATH → VS Code / Cursor extension bundle."""
    for cand in (configured, os.environ.get("CLAUDE_BIN"), shutil.which("claude")):
        if cand and Path(cand).expanduser().is_file():
            return str(Path(cand).expanduser())
    bundles = []
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        bundles += glob.glob(str(Path.home() / editor / "extensions" / "anthropic.claude-code-*" /
                                 "resources" / "native-binary" / "claude"))
    return sorted(bundles)[-1] if bundles else None


class ClaudeCode:
    backend = "claude-code"

    def __init__(self, cfg: Config, binary: str, runner=subprocess.run):
        self.cfg = cfg
        self.s = cfg.settings.llm
        self.model = self.s.model
        self.bin = binary
        self.system_text = build_system_prompt(cfg)
        self.usage = Usage()          # list-price equivalent; not billed on a subscription
        self.refusals = 0
        self.calls = 0
        self._run = runner
        self._cwd = tempfile.mkdtemp(prefix="postdoc-radar-cc-")
        self._env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}

    # -- plumbing --

    def check_budget(self) -> None:
        if self.calls >= self.s.max_claude_code_calls_per_run:
            raise BudgetExceeded(f"Claude Code call limit for this run reached ({self.calls})")

    def _call(self, prompt: str, schema: type[T], *, tools: list[str], effort: str, timeout: int,
              what: str) -> T | None:
        self.check_budget()
        self.calls += 1
        args = [
            self.bin, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(tool_schema(schema)),
            "--safe-mode", "--no-session-persistence",
            "--model", self.model, "--effort", effort,
            "--system-prompt", self.system_text,
            "--tools", ",".join(tools),
        ]
        if tools:
            args += ["--allowedTools", *tools]
        try:
            proc = self._run(args, input=prompt, capture_output=True, text=True, timeout=timeout,
                             cwd=self._cwd, env=self._env)
        except subprocess.TimeoutExpired:
            log.warning("%s: Claude Code timed out after %ds", what, timeout)
            return None
        try:
            out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            out = {}
        if not out:
            msg = (proc.stderr or proc.stdout or "").strip()[:300]
            if reason := _stop_words(msg):
                raise BudgetExceeded(f"{reason}: {msg}")
            log.warning("%s: Claude Code returned no result (exit %s): %s", what, proc.returncode, msg)
            return None
        self._record(out)
        if out.get("is_error") or out.get("subtype") != "success":
            text = str(out.get("result") or out.get("subtype") or "")
            if out.get("api_error_status") == 429:
                raise BudgetExceeded(f"Claude subscription limit reached: {text[:200]}")
            reason = _stop_words(text)
            if out.get("api_error_status") in (401, 403) or reason:
                raise BudgetExceeded(f"{reason or 'Claude Code authentication failed'}: {text[:200]}")
            if "refus" in text.lower():
                self.refusals += 1
            log.warning("%s: Claude Code error: %s", what, text[:300])
            return None
        data = out.get("structured_output")
        if data is None:
            try:
                data = json.loads(out.get("result") or "")
            except json.JSONDecodeError:
                log.warning("%s: no structured output", what)
                return None
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            log.warning("%s: structured output did not validate: %s", what, exc)
            return None

    def _record(self, out: dict) -> None:
        u = out.get("usage") or {}
        stu = u.get("server_tool_use") or {}
        self.usage.requests += 1
        self.usage.input_tokens += u.get("input_tokens", 0) or 0
        self.usage.output_tokens += u.get("output_tokens", 0) or 0
        self.usage.cache_read_tokens += u.get("cache_read_input_tokens", 0) or 0
        self.usage.cache_write_tokens += u.get("cache_creation_input_tokens", 0) or 0
        per_model = sum((m or {}).get("webSearchRequests", 0) or 0 for m in (out.get("modelUsage") or {}).values())
        self.usage.web_searches += max(stu.get("web_search_requests", 0) or 0, per_model)
        self.usage.web_fetches += stu.get("web_fetch_requests", 0) or 0
        self.usage.cost_usd += float(out.get("total_cost_usd") or 0)

    def _search_limits(self, searches: int, fetches: int) -> str:
        return (f"\n\nBudget: use at most {searches} web searches and {fetches} page fetches in total. "
                "Only use the WebSearch and WebFetch tools.")

    # -- the five tasks --

    def triage(self, batch: list[tuple[int, RawItem, list[str], list[str]]], threshold: int) -> list[TriageDecision]:
        res = self._call(triage_prompt(batch, threshold), TriageBatch, tools=[], effort=self.s.effort_triage,
                         timeout=600, what="triage")
        return res.decisions if res else []

    def analyze_page(self, url: str, text: str, context: str) -> PageAnalysis | None:
        return self._call(page_prompt(url, text, context), PageAnalysis, tools=[], effort=self.s.effort_triage,
                          timeout=600, what=f"page {url}")

    def _search(self, prompt: str, schema: type[T], searches: int, fetches: int, what: str) -> T | None:
        return self._call(prompt + self._search_limits(searches, fetches), schema,
                          tools=["WebSearch", "WebFetch"], effort=self.s.effort_search, timeout=1200, what=what)

    def search_pi(self, pi: PI, known: list[str]) -> SearchReport | None:
        return self._search(pi_task(pi, known, FINISH_JSON), SearchReport, self.s.max_web_searches_per_task,
                            self.s.max_page_fetches_per_task, f"PI search {pi.name}")

    def search_topic(self, topic: Topic, known: list[str]) -> SearchReport | None:
        return self._search(topic_task(topic, known, FINISH_JSON), SearchReport, self.s.max_web_searches_per_task,
                            self.s.max_page_fetches_per_task, f"topic search {topic.name}")

    def fellowship_status(self, f: Fellowship) -> CallStatus | None:
        return self._search(fellowship_task(f, FINISH_JSON), CallStatus, min(4, self.s.max_web_searches_per_task),
                            min(4, self.s.max_page_fetches_per_task), f"fellowship {f.id}")
