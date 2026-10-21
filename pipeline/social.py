"""Tell the outside world about new data: post to Bluesky and ping IndexNow.

Both run in GitHub Actions after the site is built. Neither stops the daily
update if it fails.

Bluesky needs two repository secrets: BLUESKY_HANDLE (e.g. insidercluster.bsky.social)
and BLUESKY_APP_PASSWORD (an app password from Bluesky settings, never the
account password). Without them, posting is skipped.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .content import bluesky_text, stories
from .layout import ROOT, config

STATE = ROOT / "data" / "social_state.json"
PDS = "https://bsky.social/xrpc"


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"bluesky_last_day": "", "bluesky_posted": []}


def save_state(state: dict) -> None:
    state["bluesky_posted"] = state["bluesky_posted"][-200:]
    STATE.write_text(json.dumps(state, indent=1) + "\n")


def link_facet(text: str, shown: str, url: str) -> dict:
    """Bluesky marks links by byte offsets in the UTF-8 text."""
    raw = text.encode("utf-8")
    start = raw.index(shown.encode("utf-8"))
    return {"index": {"byteStart": start, "byteEnd": start + len(shown.encode("utf-8"))},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]}


def post_to_bluesky(ds, site_dir: Path, dry_run: bool = False) -> str:
    handle = os.environ.get("BLUESKY_HANDLE", "").strip()
    password = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    if not dry_run and not (handle and password):
        return "Bluesky not connected (no BLUESKY_HANDLE / BLUESKY_APP_PASSWORD); skipped."
    if not ds.days:
        return "No data; skipped."
    day = ds.days[-1]
    state = load_state()
    if day <= state.get("bluesky_last_day", "") and not dry_run:
        return f"Already posted for {day}; skipped."
    picks = [s for s in stories(ds, day, limit=3) if s["key"] not in state["bluesky_posted"]]
    if not picks:
        state["bluesky_last_day"] = day
        save_state(state)
        return f"Nothing post-worthy in filings for {day}."
    story = picks[0]
    text, shown = bluesky_text(story)
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
        "facets": [link_facet(text, shown, story["url"])],
    }
    if dry_run:
        return json.dumps(record, indent=1)

    s = requests.Session()
    r = s.post(f"{PDS}/com.atproto.server.createSession",
               json={"identifier": handle, "password": password}, timeout=30)
    r.raise_for_status()
    sess = r.json()
    s.headers["Authorization"] = f"Bearer {sess['accessJwt']}"

    img = site_dir / "og" / "stock" / f"{story['ticker']}.png"
    external = {"uri": story["url"], "title": f"${story['ticker']} insider buying | {config()['site_name']}",
                "description": story["text"]}
    if img.exists() and img.stat().st_size < 950_000:
        up = s.post(f"{PDS}/com.atproto.repo.uploadBlob", data=img.read_bytes(),
                    headers={"Content-Type": "image/png"}, timeout=60)
        if up.ok:
            external["thumb"] = up.json()["blob"]
    record["embed"] = {"$type": "app.bsky.embed.external", "external": external}

    r = s.post(f"{PDS}/com.atproto.repo.createRecord",
               json={"repo": sess["did"], "collection": "app.bsky.feed.post", "record": record}, timeout=30)
    r.raise_for_status()
    state["bluesky_last_day"] = day
    state["bluesky_posted"].append(story["key"])
    save_state(state)
    return f"Posted to Bluesky: {story['text']}"


def indexnow(urls: list[str]) -> str:
    """Notify Bing (and other IndexNow engines) about new or changed pages."""
    cfg = config()
    key = cfg.get("indexnow_key")
    if not key or not urls:
        return "IndexNow: nothing to send."
    host = cfg["site_url"].split("://", 1)[1]
    r = requests.post("https://api.indexnow.org/indexnow", timeout=30, json={
        "host": host, "key": key, "keyLocation": f"{cfg['site_url']}/{key}.txt",
        "urlList": urls[:10000]})
    return f"IndexNow: sent {len(urls[:10000])} URLs, response {r.status_code}"
