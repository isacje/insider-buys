"""Command-line entry point.

  python -m pipeline.run update --days 5     # fetch any missing filing days
  python -m pipeline.run build               # rebuild site/data/insiders.json
  python -m pipeline.run demo tests/fixtures # build site data from local XML files

Each filing day is saved to data/days/YYYY-MM-DD.json holding only buys and
sells (code P and S). A day's file is written once SEC's index for that day
exists, so re-running is cheap and safe.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys

print = functools.partial(print, flush=True)  # show progress live in CI logs
from datetime import date, timedelta
from pathlib import Path

from .parse import parse_form4
from .signals import build_site_data

ROOT = Path(__file__).resolve().parent.parent
DAYS_DIR = ROOT / "data" / "days"
SITE_DATA = ROOT / "site" / "data" / "insiders.json"
KEEP_CODES = {"P", "S"}


def update(days_back: int, max_filings: int | None) -> None:
    from .edgar import Edgar  # needs network + SEC_USER_AGENT

    sec = Edgar()
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    for n in range(days_back, 0, -1):
        day = today - timedelta(days=n)
        out = DAYS_DIR / f"{day.isoformat()}.json"
        if out.exists() or day.weekday() >= 5:
            continue
        try:
            filings = sec.form4_paths(day)
        except SystemExit:
            raise
        except Exception as e:  # skip the day; the next run retries it
            print(f"{day}: couldn't read the daily index ({e}); will retry next run")
            continue
        if not filings:
            if (today - day).days > 4:  # long past: an SEC holiday, so remember it
                out.write_text("[]")
                print(f"{day}: no filings (SEC holiday)")
            else:
                print(f"{day}: no index yet (holiday, or SEC hasn't published it)")
            continue
        if max_filings:
            filings = filings[:max_filings]
        rows, failed = [], 0
        for i, f in enumerate(filings, 1):
            try:
                xml = sec.ownership_xml(f["path"])
                if xml:
                    rows += [r for r in parse_form4(xml, f["accession"], f["filed"])
                             if r["code"] in KEEP_CODES]
            except Exception as e:  # one bad filing shouldn't stop the run
                failed += 1
                print(f"  skip {f['path']}: {e}", file=sys.stderr)
            if i % 250 == 0:
                print(f"  {day}: {i}/{len(filings)} filings")
        if max_filings is None:  # partial test runs are never saved as complete
            out.write_text(json.dumps(rows, separators=(",", ":")))
        print(f"{day}: {len(filings)} Form 4s, {len(rows)} buy/sell rows, {failed} failed")


def load_rows(window_days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=window_days + CLUSTER_PAD)).isoformat()
    rows = []
    for p in sorted(DAYS_DIR.glob("*.json")):
        if p.stem >= cutoff:
            rows += json.loads(p.read_text())
    return rows


CLUSTER_PAD = 31  # look back further so clusters near the window edge still count
PAGE_WINDOW = 90  # stock pages show the last 90 days of trades


def write_site_data(data: dict) -> None:
    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    SITE_DATA.write_text(json.dumps(data, separators=(",", ":")))
    s = data["stats"]
    print(f"Wrote {SITE_DATA.relative_to(ROOT)}: {s['buy_count']} buys "
          f"({s['high_signal_buys']} high signal), {s['clusters']} clusters, "
          f"{s['sell_count']} sells")


def demo(folder: Path, as_of: date) -> None:
    rows = []
    for p in sorted(folder.glob("*.xml")):
        meta = json.loads(p.with_suffix(".json").read_text()) if p.with_suffix(".json").exists() else {}
        rows += parse_form4(p.read_text(), meta.get("accession", p.stem), meta.get("filed", as_of.isoformat()))
    from .build import build_site
    print(build_site(rows, rows, as_of, ROOT / "site"))
    data = json.loads(SITE_DATA.read_text())
    data["sample"] = True
    write_site_data(data)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="download missing filing days from SEC")
    u.add_argument("--days", type=int, default=5)
    u.add_argument("--max-filings", type=int, help="test with a few filings (not saved)")
    b = sub.add_parser("build", help="rebuild the whole website")
    b.add_argument("--window", type=int, default=30)
    n = sub.add_parser("notify", help="post to Bluesky and ping IndexNow after a build")
    n.add_argument("--bluesky", action="store_true")
    n.add_argument("--indexnow", action="store_true")
    n.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("demo", help="build site data from local sample XML")
    d.add_argument("folder", type=Path)
    d.add_argument("--as-of", default=date.today().isoformat())
    a = ap.parse_args()

    if a.cmd == "update":
        update(a.days, a.max_filings)
    elif a.cmd == "build":
        from .build import build_site
        summary = build_site(load_rows(a.window), load_rows(PAGE_WINDOW), date.today(),
                             ROOT / "site", a.window, PAGE_WINDOW)
        print("Built site: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    elif a.cmd == "notify":
        from .pages import prepare
        from .social import indexnow, post_to_bluesky
        site_dir = ROOT / "site"
        if a.bluesky:
            ds = prepare(load_rows(PAGE_WINDOW), date.today(), PAGE_WINDOW)
            try:
                print(post_to_bluesky(ds, site_dir, dry_run=a.dry_run))
            except Exception as err:  # never fail the daily update over a social post
                print(f"Bluesky post failed: {err}")
        if a.indexnow:
            changed = json.loads((site_dir / "data" / "changed.json").read_text())
            try:
                print(indexnow(changed) if not a.dry_run else f"IndexNow would send {len(changed)} URLs")
            except Exception as err:
                print(f"IndexNow failed: {err}")
    else:
        demo(a.folder, date.fromisoformat(a.as_of))


if __name__ == "__main__":
    main()
