"""Fill the homepage template with real content at build time.

The page still loads data/insiders.json for its filters and tabs, but the
summary, clusters and top buys are already in the HTML, so search engines
and people with slow connections see them immediately.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .layout import (NAV, ROOT, config, e, fmt_day, head_tags, money, promo_block,
                     signup_block, waitlist_block)
from .pages import trade_table


def cluster_items(clusters: list[dict]) -> str:
    if not clusters:
        return ('<li class="empty">No stocks have three or more insiders buying right now. '
                'Check the notable buys below.</li>')
    out = []
    for row, c in enumerate(clusters[:8]):
        marks = "".join(f'<span class="mark" style="--i:{i};--row:{row}"></span>' for i in range(len(c["insiders"])))
        names = ", ".join(f"{e(i['name'])} ({e(i['role'])})" for i in c["insiders"][:4])
        more = f", and {len(c['insiders']) - 4} more" if len(c["insiders"]) > 4 else ""
        out.append(f"""<li class="cluster">
  <div class="who"><div class="ticker"><a class="ticker-link" href="/stock/{e(c['ticker'])}/">{e(c['ticker'])}</a></div><div class="company">{e(c['company'])}</div></div>
  <div class="tally" aria-label="{c['insider_count']} insiders bought">{marks}<span class="tally-label">{c['insider_count']} insiders</span></div>
  <div class="amount">{money(c['total_value'])}</div>
  <div class="names">{names}{more}. Bought {fmt_day(c['first_date'], False)} to {fmt_day(c['last_date'], False)}.</div>
</li>""")
    return "".join(out)


def build_home(data: dict, site_dir: Path) -> None:
    cfg = config()
    s = data["stats"]
    title = "Insider Cluster: who is buying their own company's stock"
    desc = ("Which company executives and directors are buying their own stock, from SEC Form 4 filings, "
            "updated every weekday. Cluster buying, CEO purchases and every trade by ticker.")
    summary = (f"In the past {data['window_days']} days, insiders made <strong>{s['buy_count']:,} open-market "
               f"purchases worth {money(s['buy_value'])}</strong>. {s['high_signal_buys']} stand out as high signal. "
               "Executives and directors must report trades to the SEC within two business days.")
    notable = [b for b in data["buys"] if b["strength"] != "Low"][:50]
    html = (ROOT / "templates" / "index.html").read_text()
    fills = {
        "<!--HEAD-->": head_tags(title, desc, f"{cfg['site_url']}/", f"{cfg['site_url']}/og/home.png"),
        "<!--NAV-->": NAV,
        "<!--SUMMARY-->": summary,
        "<!--UPDATED-->": f"Updated {fmt_day(data['generated_at'])}",
        "<!--CLUSTERS-->": cluster_items(data["clusters"]),
        "<!--TABLE-->": trade_table(notable, "buy", show_stock=True),
        "<!--SIGNUP-->": signup_block("home"),
        "<!--WAITLIST-->": waitlist_block(),
        "<!--PROMO-->": promo_block(),
    }
    for marker, value in fills.items():
        assert marker in html, f"homepage template is missing {marker}"
        html = html.replace(marker, value)
    (site_dir / "index.html").write_text(html)
