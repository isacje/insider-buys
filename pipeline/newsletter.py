"""Create the weekly digest as a Kit broadcast, scheduled for Monday morning.

Runs in GitHub Actions on Saturdays, after Friday's filings are in. The email
lands in Kit as a scheduled broadcast, so it can be previewed, edited or
cancelled over the weekend; if nothing is done it sends itself Monday at
12:00 UTC (8am New York in summer, 7am in winter).

Needs the KIT_API_KEY repository secret (a v4 API key from Kit:
Settings -> Developer). Without it, nothing is sent.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta, timezone

import requests

from .content import notable_buys, week_id, week_label, week_slice, weekly_email
from .layout import config, money
from .social import load_state, save_state

API = "https://api.kit.com/v4/broadcasts"


def monday_after(day: date) -> datetime:
    days = (7 - day.weekday()) % 7 or 7
    return datetime.combine(day + timedelta(days=days), time(12, 0), tzinfo=timezone.utc)


def preview_text(ds, wid: str) -> str:
    _, buys, _, clusters = week_slice(ds, wid)
    top = notable_buys(buys)[:2]
    bits = [f"{c['insider_count']} insiders bought ${c['ticker']}" for c in clusters[:1]]
    bits += [f"${b['ticker']} {money(b['value'])}" for b in top]
    return ("This week: " + ", ".join(bits) + ".") if bits else "This week's insider purchases from SEC filings."


def build_broadcast(ds, wid: str, send_at: datetime | None) -> dict:
    html, subject = weekly_email(ds, wid)
    return {
        "subject": subject,
        "content": html,
        "description": f"Weekly digest {wid} (created automatically by insidercluster.net)",
        "preview_text": preview_text(ds, wid),
        "public": False,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "send_at": send_at.isoformat() if send_at else None,
    }


def due_week(ds, today: date) -> str | None:
    """On Saturday (or later in the weekend), the week that just ended."""
    if not ds.days or today.weekday() < 5:
        return None
    wid = week_id(ds.days[-1])
    return wid if wid == week_id(today.isoformat()) else None


def send_weekly(ds, today: date, *, draft_now: bool = False, dry_run: bool = False) -> str:
    key = os.environ.get("KIT_API_KEY", "").strip()
    if not key and not dry_run:
        return "Kit not connected (no KIT_API_KEY); skipped."
    state = load_state()
    sent = state.setdefault("newsletter_weeks", [])

    if draft_now:
        wid = week_id(ds.days[-1]) if ds.days else None
        send_at = None
    else:
        wid = due_week(ds, today)
        send_at = monday_after(today) if wid else None
        if not wid:
            return "Not newsletter day (runs Saturdays); skipped."
        if wid in sent:
            return f"Newsletter for {wid} already created; skipped."
    if not wid:
        return "No data; skipped."

    body = build_broadcast(ds, wid, send_at)
    if dry_run:
        return json.dumps({k: (v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v)
                           for k, v in body.items()}, indent=1)

    r = requests.post(API, json=body, timeout=30,
                      headers={"X-Kit-Api-Key": key, "Accept": "application/json"})
    if r.status_code >= 400:
        return f"Kit refused the broadcast ({r.status_code}): {r.text[:300]}"
    b = r.json().get("broadcast", {})
    if not draft_now:
        sent.append(wid)
        save_state(state)
    when = f"scheduled for {send_at:%a %b %d %H:%M} UTC" if send_at else "saved as a draft"
    return f"Created Kit broadcast {b.get('id')} for {week_label(wid)}, {when}: {body['subject']}"
