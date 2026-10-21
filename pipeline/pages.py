"""Stock pages, the A-Z list, and the shared data preparation every page uses.

Pages are plain HTML (no JavaScript needed), so search engines can read
them directly. They are rebuilt from scratch on every run.
"""
from __future__ import annotations

import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .layout import config, e, fmt_day, money, nice_company, page, sec_url
from .signals import (find_clusters, flag_bad_prices, flag_plan_purchases,
                      group_trades, score_buy)

SAFE_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
NEGATIVE_REASON = re.compile(r"scheduled|indirectly|plan\)", re.I)
CLUSTER_WINDOW = 30


@dataclass
class Dataset:
    as_of: date
    window_days: int
    buys: list[dict]
    sells: list[dict]
    clusters: dict[str, dict]          # current clusters, by ticker
    days: list[str] = field(default_factory=list)   # filing days with data, ascending

    def safe(self, trades: list[dict]) -> list[dict]:
        return [t for t in trades if SAFE_TICKER.match(t["ticker"])]


def prepare(rows: list[dict], as_of: date, window_days: int = 90) -> Dataset:
    """Clean, de-duplicate, flag and score every trade once for all pages."""
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
    c_start = (as_of - timedelta(days=CLUSTER_WINDOW)).isoformat()
    clusters = {c["ticker"]: c for c in find_clusters(
        [b for b in buys if b["filed"] >= c_start], as_of)}
    for b in buys:
        # a buy only earns "other insiders buying" if it's inside the current cluster window
        score_buy(b, set(clusters) if b["trade_date"] >= c_start else set())
    days = sorted({t["filed"] for t in recent})
    return Dataset(as_of, window_days, buys, sells, clusters, days)


def clusters_as_of(ds: Dataset, day: str) -> dict[str, dict]:
    """Clusters as they stood at the end of a given filing day."""
    d = date.fromisoformat(day)
    start = (d - timedelta(days=CLUSTER_WINDOW)).isoformat()
    return {c["ticker"]: c for c in find_clusters(
        [b for b in ds.buys if start <= b["filed"] <= day], d)}


def stock_link(ticker: str) -> str:
    return f'<a class="ticker-link" href="/stock/{e(ticker)}/">{e(ticker)}</a>'


def trade_table(trades: list[dict], side: str, show_stock: bool = False) -> str:
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
        stock = (f'<td data-col="stock"><div class="t-ticker">{stock_link(t["ticker"])}</div>'
                 f'<div class="t-company">{e(nice_company(t["company"]))}</div></td>') if show_stock else ""
        rows.append(f"""<tr>
  {stock}<td data-col="insider"><div class="t-insider">{e(t['insider'])}</div><div class="t-role">{e(t['title'] or t['role'])}{also}</div></td>
  <td class="num" data-col="value"><strong>{money(t['value'])}</strong><div class="t-date">{t['shares']:,} sh @ {price}</div></td>
  <td class="num" data-col="change">{change}</td>
  <td data-col="why">{signal}</td>
  <td data-col="dates" class="t-date">{fmt_day(t['trade_date'])}<br><a href="{sec_url(t)}" rel="nofollow noopener" target="_blank">Filed {fmt_day(t['filed'])}</a></td>
</tr>""")
    head = "Signal" if side == "buy" else "Plan"
    stock_th = "<th>Stock</th>" if show_stock else ""
    return (f'<table><thead><tr>{stock_th}<th>Insider</th><th class="num">Value</th><th class="num">Holdings</th>'
            f'<th>{head}</th><th>Traded / filed</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def stock_summary(ticker: str, buys: list[dict], sells: list[dict]) -> str:
    """One line used in descriptions and link previews."""
    buyers = {b["insider_cik"] for b in buys}
    sellers = {s["insider_cik"] for s in sells}
    buy_v = sum(b["value"] for b in buys)
    sell_v = sum(s["value"] for s in sells)
    if buys and sells:
        return (f"{len(buyers)} insider{'s' if len(buyers) != 1 else ''} bought {money(buy_v)} and "
                f"{len(sellers)} sold {money(sell_v)}")
    if buys:
        return f"{len(buyers)} insider{'s' if len(buyers) != 1 else ''} bought {money(buy_v)}"
    return f"{len(sellers)} insider{'s' if len(sellers) != 1 else ''} sold {money(sell_v)}"


def stock_page(ticker: str, buys: list[dict], sells: list[dict], cluster: dict | None,
               window_days: int, has_image: bool) -> str:
    site_url = config()["site_url"]
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

    description = (f"{ticker} ({company}) insider trading from SEC Form 4 filings: "
                   f"{stock_summary(ticker, buys, sells)} in the past {window_days} days. "
                   "Every trade by executives and directors, updated daily.")
    title = f"{ticker} insider buying and selling: {company} | {config()['site_name']}"

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
<section class="block" aria-labelledby="sells-h">
  <h2 id="sells-h">Insider sales</h2>
  {trade_table(sells, "sell")}
</section>"""
    image = f"{site_url}/og/stock/{ticker}.png" if has_image else None
    return page(title, description, f"{site_url}/stock/{ticker}/", body, image=image)


def az_page(index: list[tuple[str, str, int, int]]) -> str:
    site_url = config()["site_url"]
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
    return page(f"All stocks with recent insider trades | {config()['site_name']}",
                "A to Z list of every stock with recent insider buying or selling, from SEC Form 4 filings.",
                f"{site_url}/stock/", body)


def build_stock_pages(ds: Dataset, site_dir: Path, images: set[str]) -> list[str]:
    """Write /stock/TICKER/ pages and the A-Z list. Returns the tickers written."""
    by_ticker: dict[str, dict[str, list]] = defaultdict(lambda: {"buy": [], "sell": []})
    for t in ds.safe(ds.buys + ds.sells):
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
        (d / "index.html").write_text(stock_page(ticker, b, s, ds.clusters.get(ticker),
                                                 ds.window_days, ticker in images))
        index.append((ticker, nice_company((b or s)[0]["company"]), len(b), len(s)))
    (out / "index.html").write_text(az_page(index))
    return [t for t, *_ in index]


def stock_groups(ds: Dataset) -> dict[str, dict[str, list]]:
    by_ticker: dict[str, dict[str, list]] = defaultdict(lambda: {"buy": [], "sell": []})
    for t in ds.safe(ds.buys + ds.sells):
        by_ticker[t["ticker"]][t["side"]].append(t)
    return by_ticker
