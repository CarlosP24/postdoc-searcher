"""Notifications about new interesting offers.

Channels:
- GitHub issue (default on GitHub Actions): the run opens an issue assigned to the
  repo owner, and GitHub emails it to you. Uses the workflow's built-in
  GITHUB_TOKEN, so no password is stored anywhere.
- SMTP email (optional): only if SMTP_USER, SMTP_PASSWORD and EMAIL_TO are set.
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl

import requests
from datetime import date
from email.message import EmailMessage

from .config import Settings
from .models import Opportunity
from .util import parse_date

log = logging.getLogger(__name__)

STATUS_LABEL = {"open": "Open", "rolling": "Rolling / always open", "upcoming": "Opens soon",
                "unknown": "Open (no deadline stated)", "closed": "Closed"}


def pending_events(items: list[Opportunity], threshold: int) -> list[tuple[str, Opportunity]]:
    """('new' | 'opened', opportunity) for everything worth an email."""
    events = []
    for o in items:
        if o.relevance < threshold or o.status == "closed":
            continue
        if o.notified_status is None:
            events.append(("new", o))
        elif o.status in ("open", "rolling") and not o.dates_estimated and (
                o.notified_status == "upcoming" or (o.kind == "fellowship" and o.notified_status == "unknown")):
            events.append(("opened", o))
    events.sort(key=lambda e: (e[0] != "opened", -e[1].relevance))
    return events


def upcoming_deadlines(items: list[Opportunity], threshold: int, days: int, ref: date,
                       exclude: set[str]) -> list[Opportunity]:
    out = []
    for o in items:
        d = parse_date(o.deadline, end_of_month=True)
        if (o.id in exclude or o.relevance < threshold or o.status not in ("open", "rolling")
                or not d or o.dates_estimated):
            continue
        if 0 <= (d - ref).days <= days:
            out.append(o)
    return sorted(out, key=lambda o: o.deadline or "")


def _status_label(o: Opportunity) -> str:
    if o.kind == "fellowship" and o.status == "unknown":
        return "Call dates not known yet"
    return STATUS_LABEL.get(o.status, o.status)


def _period(o: Opportunity, ref: date) -> str:
    approx = "≈ " if o.dates_estimated else ""
    if o.rolling and not o.deadline:
        return "Rolling — apply any time"
    bits = []
    if o.call_opens:
        bits.append(f"opens {approx}{o.call_opens}")
    if o.deadline:
        d = parse_date(o.deadline, end_of_month=True)
        left = f" ({(d - ref).days} days left)" if d and d >= ref else ""
        bits.append(f"deadline {approx}{o.deadline}{left}")
    if not bits:
        return "not known yet — checked automatically" if o.kind == "fellowship" else "dates not stated"
    return ", ".join(bits)


def _card(kind: str, o: Opportunity, ref: date) -> str:
    e = html.escape
    badge = "CALL NOW OPEN" if kind == "opened" else ("FELLOWSHIP" if o.kind == "fellowship" else "POSITION")
    color = "#b45309" if kind == "opened" else ("#6d28d9" if o.kind == "fellowship" else "#0f766e")
    where = " · ".join(x for x in [o.institution, o.location or o.country] if x)
    pis = ""
    if o.kind == "fellowship" and o.matched_pis:
        pis = "<br>".join(f"<b>{e(p)}</b>: {e(o.pi_notes.get(p, ''))}" for p in o.matched_pis)
        pis = f'<div style="margin:6px 0"><span style="color:#555">Possible host PIs:</span><br>{pis}</div>'
    elif o.pi or o.matched_pis:
        names = [o.pi] if o.pi else []
        names += [p for p in o.matched_pis if p != o.pi]
        pis = f'<div style="margin:4px 0"><span style="color:#555">PI:</span> <b>{e(", ".join(names))}</b></div>'
    cond = " · ".join(e(x) for x in [o.duration and f"Duration: {o.duration}", o.salary and f"Salary: {o.salary}",
                                      o.start_date and f"Start: {o.start_date}"] if x)
    return f"""
<div style="border:1px solid #e5e7eb;border-left:4px solid {color};border-radius:8px;padding:12px 14px;margin:0 0 14px">
  <div style="font-size:11px;font-weight:700;color:{color};letter-spacing:.04em">{badge} · {_status_label(o)} · relevance {o.relevance}</div>
  <div style="font-size:16px;font-weight:600;margin:4px 0"><a href="{e(o.url)}" style="color:#111827">{e(o.title)}</a></div>
  <div style="color:#374151">{e(where)}</div>
  {pis}
  <div style="margin:4px 0"><span style="color:#555">Call period:</span> {e(_period(o, ref))}</div>
  {f'<div style="margin:4px 0;color:#374151">{cond}</div>' if cond else ''}
  <div style="margin:6px 0;color:#111827">{e(o.summary)}</div>
  {f'<div style="margin:4px 0;color:#374151"><i>Eligibility:</i> {e(o.eligibility)}</div>' if o.eligibility and o.kind == 'fellowship' else ''}
  <div style="font-size:12px;color:#6b7280">Why: {e(o.relevance_reason)}</div>
</div>"""


def compose(events, reminders, settings: Settings, ref: date) -> EmailMessage:
    n_new = sum(1 for k, _ in events if k == "new")
    n_open = sum(1 for k, _ in events if k == "opened")
    bits = []
    if n_new:
        bits.append(f"{n_new} new offer{'s' if n_new != 1 else ''}")
    if n_open:
        bits.append(f"{n_open} call{'s' if n_open != 1 else ''} now open")
    subject = f"{settings.email.subject_prefix} {', '.join(bits) or 'update'} — {ref.isoformat()}"
    top = sorted(events, key=lambda e: -e[1].relevance)[0][1] if events else None
    if top:
        subject += f" (top: {top.title[:60]})"

    dash = settings.dashboard.url
    cards = "".join(_card(k, o, ref) for k, o in events)
    rem = ""
    if reminders:
        rows = "".join(
            f'<li><a href="{html.escape(o.url)}">{html.escape(o.title)}</a> — deadline <b>{o.deadline}</b>'
            f'{" · PI: " + html.escape(", ".join(o.matched_pis or ([o.pi] if o.pi else []))) if (o.matched_pis or o.pi) else ""}</li>'
            for o in reminders)
        rem = f'<h3 style="margin:22px 0 8px">⏰ Deadlines in the next {settings.email.deadline_reminder_days} days</h3><ul>{rows}</ul>'
    body = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:720px;margin:auto;color:#111827">
<h2 style="margin:0 0 4px">{html.escape(settings.dashboard.title)}</h2>
<div style="color:#6b7280;margin:0 0 16px">{html.escape(', '.join(bits) or 'update')} · {ref.isoformat()}{f' · <a href="{html.escape(dash)}">open dashboard</a>' if dash else ''}</div>
{cards}{rem}
<div style="color:#9ca3af;font-size:12px;margin-top:24px">Sent by Postdoc Radar. Dates marked ≈ are estimates from previous calls — always confirm on the official page.</div>
</div>"""

    text_lines = [f"{settings.dashboard.title} — {', '.join(bits)}", ""]
    for k, o in events:
        text_lines += [
            f"[{'NOW OPEN' if k == 'opened' else o.kind.upper()}] {o.title} (relevance {o.relevance})",
            f"  {' · '.join(x for x in [o.institution, o.location or o.country] if x)}",
            f"  PI: {', '.join(o.matched_pis or ([o.pi] if o.pi else [])) or '-'}",
            f"  Call: {_period(o, ref)}",
            f"  {o.summary}",
            f"  {o.url}", "",
        ]
    if dash:
        text_lines.append(f"Dashboard: {dash}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg.set_content("\n".join(text_lines))
    msg.add_alternative(body, subtype="html")
    return msg


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD") and os.environ.get("EMAIL_TO"))


def send(msg: EmailMessage) -> None:
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    to = [a.strip() for a in os.environ["EMAIL_TO"].split(",") if a.strip()]
    msg["From"] = os.environ.get("EMAIL_FROM") or user
    msg["To"] = ", ".join(to)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
    log.info("email sent to %s: %s", msg["To"], msg["Subject"])


# ── GitHub issue channel ────────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"
_MAX_BODY = 60000  # GitHub's issue body limit is 65 536 characters


def _md(text: str | None) -> str:
    return (text or "").replace("\n", " ").replace("|", "\\|").strip()


def _md_card(kind: str, o: Opportunity, ref: date) -> str:
    badge = "🟠 **CALL NOW OPEN**" if kind == "opened" else ("🟣 **Fellowship**" if o.kind == "fellowship"
                                                             else "🟢 **Position**")
    where = " · ".join(x for x in [o.institution, o.location or o.country] if x)
    lines = [f"### [{_md(o.title)}]({o.url})",
             f"{badge} · {_status_label(o)} · relevance **{o.relevance}**" + (f" · {_md(where)}" if where else "")]
    if o.kind == "fellowship" and o.matched_pis:
        lines.append("**Possible host PIs:**\n" +
                     "\n".join(f"- **{p}**: {_md(o.pi_notes.get(p, ''))}" for p in o.matched_pis))
    elif o.pi or o.matched_pis:
        names = ([o.pi] if o.pi else []) + [p for p in o.matched_pis if p != o.pi]
        lines.append(f"**PI:** {', '.join(names)}")
    lines.append(f"**Call period:** {_period(o, ref)}")
    cond = " · ".join(x for x in [o.duration and f"Duration: {o.duration}", o.salary and f"Salary: {o.salary}",
                                   o.start_date and f"Start: {o.start_date}"] if x)
    if cond:
        lines.append(_md(cond))
    if o.summary:
        lines.append(_md(o.summary))
    if o.eligibility and o.kind == "fellowship":
        lines.append(f"*Eligibility:* {_md(o.eligibility)}")
    if o.relevance_reason:
        lines.append(f"<sub>Why: {_md(o.relevance_reason)}</sub>")
    return "\n\n".join(lines)


def compose_markdown(events, reminders, settings: Settings, ref: date) -> tuple[str, str]:
    """(title, body) of the digest as a GitHub issue."""
    msg_subject = compose(events, reminders, settings, ref)["Subject"]
    title = msg_subject.removeprefix(settings.email.subject_prefix).strip()
    dash = settings.dashboard.url
    parts = [f"**{len(events)} item{'s' if len(events) != 1 else ''}** · {ref.isoformat()}"
             + (f" · [open dashboard]({dash})" if dash else "")]
    parts += [_md_card(k, o, ref) for k, o in events]
    if reminders:
        rows = "\n".join(
            f"- [{_md(o.title)}]({o.url}) — deadline **{o.deadline}**"
            + (f" · PI: {', '.join(o.matched_pis or [o.pi])}" if (o.matched_pis or o.pi) else "")
            for o in reminders)
        parts.append(f"### ⏰ Deadlines in the next {settings.email.deadline_reminder_days} days\n\n{rows}")
    parts.append("<sub>Opened by Postdoc Radar. Dates marked ≈ are estimates from previous calls — always "
                 "confirm on the official page. Close this issue once you've read it.</sub>")
    body = "\n\n---\n\n".join(parts)
    if len(body) > _MAX_BODY:
        body = body[:_MAX_BODY] + "\n\n… (truncated — see the dashboard for the rest)"
    return title, body


def github_configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY"))


def open_github_issue(title: str, body: str, assignee: str = "") -> str:
    """Open an issue in this repository, assigned to `assignee` (default: the repo owner). Returns its URL."""
    repo = os.environ["GITHUB_REPOSITORY"]
    who = assignee or os.environ.get("GITHUB_REPOSITORY_OWNER") or repo.split("/")[0]
    headers = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    payload = {"title": title[:250], "body": body, "assignees": [who]}
    r = requests.post(f"{GITHUB_API}/repos/{repo}/issues", json=payload, headers=headers, timeout=30)
    if r.status_code == 422:  # assignee not allowed (e.g. org repo) — open it unassigned
        payload.pop("assignees")
        r = requests.post(f"{GITHUB_API}/repos/{repo}/issues", json=payload, headers=headers, timeout=30)
    r.raise_for_status()
    url = r.json().get("html_url", "")
    log.info("opened GitHub issue %s", url)
    return url


def channels(settings: Settings) -> list[str]:
    """Notification channels usable in this environment."""
    out = []
    if settings.email.github_issues and github_configured():
        out.append("github")
    if settings.email.smtp and smtp_configured():
        out.append("smtp")
    return out


def deliver(events, reminders, settings: Settings, ref: date, chans: list[str]) -> list[str]:
    """Send the digest through each channel. Returns the channels that succeeded; raises if all failed."""
    ok, errors = [], []
    for ch in chans:
        try:
            if ch == "github":
                title, body = compose_markdown(events, reminders, settings, ref)
                open_github_issue(title, body, settings.email.github_assignee)
            else:
                send(compose(events, reminders, settings, ref))
            ok.append(ch)
        except Exception as exc:  # noqa: BLE001 — try the other channel, report below
            errors.append(f"{ch}: {exc}")
    if not ok and errors:
        raise RuntimeError("; ".join(errors))
    for e in errors:
        log.warning("notification channel failed: %s", e)
    return ok
