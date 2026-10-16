"""Download Form 4 filings from SEC EDGAR.

SEC rules for automated access (https://www.sec.gov/os/accessing-edgar-data):
  * Send a User-Agent that identifies you, e.g. "Your Name you@example.com".
  * Stay under 10 requests per second.
We default to ~8 requests/second and retry politely on 429/5xx.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date

import requests

ARCHIVES = "https://www.sec.gov/Archives"
FORM_TYPES = {"4"}  # amendments (4/A) are skipped in v1 to avoid double counting

# Lines in form.YYYYMMDD.idx look like:
# 4           Apple Inc.                         320193      20261015    edgar/data/320193/0001234567-26-000123.txt
_IDX_LINE = re.compile(
    r"^(?P<form>\S+(?: \S+)?)\s{2,}(?P<company>.+?)\s{2,}(?P<cik>\d+)\s+"
    r"(?P<filed>\d{8}|\d{4}-\d{2}-\d{2})\s+(?P<path>edgar/\S+)\s*$"
)
_XML_BLOCK = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.S)


class Edgar:
    def __init__(self, user_agent: str | None = None, max_rps: float = 8.0):
        ua = user_agent or os.environ.get("SEC_USER_AGENT")
        if not ua or "@" not in ua:
            raise SystemExit(
                'Set SEC_USER_AGENT to "Your Name your@email.com" (SEC requires it).'
            )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self.min_interval = 1.0 / max_rps
        self._last = 0.0

    def get(self, url: str) -> requests.Response | None:
        """GET with rate limiting and retries. Returns None on 404."""
        for attempt in range(5):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"Giving up on {url}")

    def form4_paths(self, day: date) -> list[dict]:
        """List Form 4 filings filed on `day` (empty on weekends/holidays)."""
        qtr = (day.month - 1) // 3 + 1
        url = f"{ARCHIVES}/edgar/daily-index/{day.year}/QTR{qtr}/form.{day:%Y%m%d}.idx"
        r = self.get(url)
        return parse_index(r.text) if r is not None else []

    def ownership_xml(self, path: str) -> str | None:
        """Fetch a filing's full submission text and return its Form 4 XML."""
        r = self.get(f"{ARCHIVES}/{path}")
        return extract_xml(r.text) if r is not None else None


def parse_index(text: str) -> list[dict]:
    """Parse a daily form index into unique Form 4 filings.

    Each filing is listed once per filer (issuer and each insider), under
    each filer's folder, so we de-duplicate on the accession number.
    """
    seen: dict[str, dict] = {}
    for line in text.splitlines():
        m = _IDX_LINE.match(line.rstrip())
        if not m or m["form"] not in FORM_TYPES:
            continue
        path = m["path"]
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        if accession not in seen:
            filed = m["filed"].replace("-", "")
            seen[accession] = {
                "path": path,
                "accession": accession,
                "filed": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}",
            }
    return list(seen.values())


def extract_xml(submission: str) -> str | None:
    m = _XML_BLOCK.search(submission)
    return m.group(0) if m else None
