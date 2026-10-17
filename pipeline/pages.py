"""Generate a static page per stock, an A-Z stock list, a sitemap and robots.txt.

Each page is plain HTML (no JavaScript needed), so search engines can read
it directly. Pages are rebuilt from scratch on every run.
"""
from __future__ import annotations

import html
import re
import shutil
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .signals import (find_clusters, flag_bad_prices, flag_plan_purchases,
                      group_trades, score_buy)

SITE_NAME = "Insider Cluster"
SAFE_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
NEGATIVE_REASON = re.compile(r"scheduled|indirectly|plan\)", re.I)


def money(v: float) -> str:
    v = v or 0
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def fmt_day(s: str) -> str:
    try:
        d = date.fromisoformat(s)
    except (TypeError, ValueError):
        return s or ""
    return f"{d:%b} {d.day}, {d.year}"


def sec_url(t: dict) -> str:
    acc = t["accession"]
    return (f"https://www.sec.gov/Archives/edgar/data/{t['issuer_cik']}/"
            f"{acc.replace('-', '')}/{acc}-index.htm")


def nice_company(name: str) -> str:
    """'FULLER H B CO' -> 'Fuller H B Co'; leave mixed-case names alone."""
    return name.title() if name.isupper() else name


e = html.escape


def page(title: str, description: str, canonical: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{e(canonical)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:ital,wght@0,400;0,500;0,600;0,800;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="/">{SITE_NAME}</a>
  <a class="updated" href="/stock/">All stocks</a>
</div></header>
<main class="wrap">
{body}
</main>
<footer><div class="wrap">
  <p>Data comes from Form 4 filings on SEC EDGAR, updated every weekday morning. Open-market purchases and sales only; option exercises, grants and gifts are left out.</p>
  <p>This is information, not investment advice. Insiders buy and sell for many reasons, and a trade doesn't predict where the stock will go.</p>
</div></footer>
</body>
</html>
"""


def trade_table(trades: list[dict], side: str) -> str:
    if not trades:
        return ('<div class="empty">No open-market '
                f'{"purchases" if side == "buy" else "sales"} by insiders in this period.</div>')
    rows = []
    for t in trades:
        if t.get("new_position"):
            change = '<span class="up">New position</span>'
        elif t.get("holding_change_pct") is None:
            change = "–"
        else:
            sign, cls = ("+", "up") if side == "buy" else ("−", "")
            change = f'<span class="{cls}">{sign}{t["holding_change_pct"]}%</span>'
        if side == "buy":
            reasons = "".join(
                f'<li class="{"neg" if NEGATIVE_REASON.search(r) else ""}">{e(r)}</li>'
                for r in t["reasons"])
            signal = f'<span class="badge s-{t["strength"]}">{t["strength"]}</span><ul class="reasons">{reasons}</ul>'
        else:
            signal = ('<span class="badge s-sell">'
                      f'{"Pre-scheduled" if t["plan_10b5_1"] else "Not pre-scheduled"}</span>')
        also = f"<br>Also reported by {e(t['also_reported_by'])}" if t.get("also_reported_by") else ""
        price = f"${t['avg_price']:,.2f}" if t.get("avg_price") is not None else "–"
        rows.append(f"""<tr>
  <td data-col="insider"><div class="t-insider">{e(t['insider'])}</div><div class="t-role">{e(t['title'] or t['role'])}{also}</div></td>
  <td class="num" data-col="value"><strong>{money(t['value'])}</strong><div class="t-date">{t['shares']:,} sh @ {price}</div></td>
  <td class="num" data-col="change">{change}</td>
  <td data-col="why">{signal}</td>
  <td data-col="dates" class="t-date">{fmt_day(t['trade_date'])}<br><a href="{sec_url(t)}" rel="nofollow noopener" target="_blank">Filed {fmt_day(t['filed'])}</a></td>
</tr>""")
    head = "Signal" if side == "buy" else "Plan"
    return (f'<table><thead><tr><th>Insider</th><th class="num">Value</th><th class="num">Holdings</th>'
            f'<th>{head}</th><th>Traded / filed</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def stock_page(ticker: str, buys: list[dict], sells: list[dict], cluster: dict | None,
               window_days: int, site_url: str) -> str:
    company = nice_company((buys or sells)[0]["company"])
    buyers = {b["insider_cik"] for b in buys}
    sellers = {s["insider_cik"] for s in sells}
    buy_v = sum(b["value"] for b in buys)
    sell_v = sum(s["value"] for s in sells)

    parts = []
    if buys:
        parts.append(f"{len(buyers)} insider{'s' if len(buyers) != 1 else ''} bought "
                     f"<strong>{money(buy_v)}</strong> of {e(company)} stock in "
                     f"{len(buys)} open-market purchase{'s' if len(buys) != 1 else ''}")
    if sells:
        parts.append(f"{len(sellers)} insider{'s' if len(sellers) != 1 else ''} sold "
                     f"<strong>{money(sell_v)}</strong>")
    lede = f"In the past {window_days} days, " + (", and ".join(parts) if parts else "no insiders traded") + "."
    if buys:
        top = max(buys, key=lambda b: b["value"])
        lede += (f" The largest purchase was {money(top['value'])} by {e(top['insider'])}"
                 f" ({e(top['title'] or top['role'])}) on {fmt_day(top['trade_date'])}.")

    callout = ""
    if cluster:
        callout = (f'<p class="callout"><strong>Cluster buying:</strong> {cluster["insider_count"]} '
                   f'different insiders bought {money(cluster["total_value"])} between '
                   f'{fmt_day(cluster["first_date"])} and {fmt_day(cluster["last_date"])}.</p>')

    if buys and sells:
        desc_core = f"{len(buyers)} insiders bought {money(buy_v)} and {len(sellers)} sold {money(sell_v)}"
    elif buys:
        desc_core = f"{len(buyers)} insider{'s' if len(buyers) != 1 else ''} bought {money(buy_v)}"
    else:
        desc_core = f"{len(sellers)} insider{'s' if len(sellers) != 1 else ''} sold {money(sell_v)}"
    description = (f"{ticker} ({company}) insider trading from SEC Form 4 filings: {desc_core} "
                   f"in the past {window_days} days. Every trade by executives and directors, updated daily.")
    title = f"{ticker} insider buying and selling: {company} | {SITE_NAME}"

    body = f"""<nav class="crumbs"><a href="/">Home</a> / <a href="/stock/">All stocks</a> / {e(ticker)}</nav>
<section class="stock-head">
  <h1>{e(ticker)} insider trading</h1>
  <p class="co">{e(company)}</p>
  <p class="lede">{lede}</p>
  {callout}
</section>
<section class="block" aria-labelledby="buys-h">
  <h2 id="buys-h">Insider purchases</h2>
  {trade_table(buys, "buy")}
</section>
<section class="block ledger" aria-labelledby="sells-h">
  <h2 id="sells-h">Insider sales</h2>
  {trade_table(sells, "sell")}
</section>"""
    return page(title, description, f"{site_url}/stock/{ticker}/", body)


def az_page(index: list[tuple[str, str, int, int]], site_url: str) -> str:
    items, letter = [], None
    for ticker, company, nb, ns in index:
        first = ticker[0] if ticker[0].isalpha() else "#"
        if first != letter:
            letter = first
            items.append(f'<li class="az-letter">{letter}</li>')
        activity = ", ".join(x for x in (f"{nb} buy{'s' if nb != 1 else ''}" if nb else "",
                                         f"{ns} sell{'s' if ns != 1 else ''}" if ns else "") if x)
        items.append(f'<li><a href="/stock/{ticker}/">{e(ticker)}</a> '
                     f'<span>{e(company[:34])}</span><span>{activity}</span></li>')
    body = f"""<nav class="crumbs"><a href="/">Home</a> / All stocks</nav>
<section class="stock-head">
  <h1>All stocks with insider trades</h1>
  <p class="lede">{len(index):,} stocks where executives, directors or large shareholders bought or sold shares on the open market recently.</p>
</section>
<ul class="az">{"".join(items)}</ul>"""
    return page(f"All stocks with recent insider trades | {SITE_NAME}",
                "A to Z list of every stock with recent insider buying or selling, from SEC Form 4 filings.",
                f"{site_url}/stock/", body)


def build_pages(rows: list[dict], as_of: date, site_dir: Path, site_url: str,
                window_days: int = 90, cluster_window: int = 30) -> int:
    trades = group_trades(rows)
    bad = {id(t) for t in flag_bad_prices(trades)}
    # Say how far back the data really goes (the archive starts small and grows).
    earliest = min((t["filed"] for t in trades), default=as_of.isoformat())
    window_days = max(1, min(window_days, (as_of - date.fromisoformat(earliest)).days))
    start = (as_of - timedelta(days=window_days)).isoformat()
    recent = [t for t in trades if t["filed"] >= start and id(t) not in bad and t["value"] > 0]
    buys = [t for t in recent if t["side"] == "buy"]
    sells = [t for t in recent if t["side"] == "sell"]

    flag_plan_purchases(buys)
    c_start = (as_of - timedelta(days=cluster_window)).isoformat()
    clusters = {c["ticker"]: c for c in find_clusters(
        [b for b in buys if b["filed"] >= c_start], as_of)}
    for b in buys:
        # a buy only earns "other insiders buying" if it's inside the current cluster window
        score_buy(b, set(clusters) if b["trade_date"] >= c_start else set())

    by_ticker: dict[str, dict[str, list]] = defaultdict(lambda: {"buy": [], "sell": []})
    for t in buys + sells:
        if SAFE_TICKER.match(t["ticker"]):
            by_ticker[t["ticker"]][t["side"]].append(t)

    out = site_dir / "stock"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    index = []
    for ticker in sorted(by_ticker):
        b = sorted(by_ticker[ticker]["buy"], key=lambda t: (t["trade_date"], t["value"]), reverse=True)
        s = sorted(by_ticker[ticker]["sell"], key=lambda t: (t["trade_date"], t["value"]), reverse=True)
        d = out / ticker
        d.mkdir()
        (d / "index.html").write_text(stock_page(ticker, b, s, clusters.get(ticker), window_days, site_url))
        index.append((ticker, nice_company((b or s)[0]["company"]), len(b), len(s)))

    (out / "index.html").write_text(az_page(index, site_url))

    urls = [f"{site_url}/", f"{site_url}/stock/"] + [f"{site_url}/stock/{t}/" for t, *_ in index]
    (site_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{e(u)}</loc><lastmod>{as_of.isoformat()}</lastmod></url>\n" for u in urls)
        + "</urlset>\n")
    (site_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {site_url}/sitemap.xml\n")
    (site_dir / "CNAME").write_text(site_url.split("://", 1)[-1] + "\n")
    return len(index)
