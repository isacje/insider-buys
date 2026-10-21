"""Shared HTML pieces: settings, formatting, the page shell, signup forms,
link-preview tags and analytics. Everything that depends on an outside
account (Kit, Google Analytics) is switched off until its ID is in site.json.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
e = html.escape


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = json.loads((ROOT / "site.json").read_text())
    cfg["site_url"] = cfg["site_url"].rstrip("/")
    return cfg


# ---------- formatting ----------

def money(v: float) -> str:
    v = v or 0
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def fmt_day(s: str, year: bool = True) -> str:
    try:
        d = date.fromisoformat(s)
    except (TypeError, ValueError):
        return s or ""
    return f"{d:%b} {d.day}, {d.year}" if year else f"{d:%b} {d.day}"


def nice_company(name: str) -> str:
    """'FULLER H B CO' -> 'Fuller H B Co'; leave mixed-case names alone."""
    return name.title() if name.isupper() else name


_SUFFIX = re.compile(r"[,\s]+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|"
                     r"l\.?p|llc|n\.?v|s\.?a|ag|se|/de/|/md/|/new/)\.?$", re.I)


def short_company(name: str) -> str:
    """'GameStop Corp.' -> 'GameStop'; 'Lennar Corp /New/' -> 'Lennar'. For prose."""
    n = nice_company(name).strip()
    for _ in range(3):
        n2 = _SUFFIX.sub("", n).strip(" ,")
        if n2 == n or not n2:
            break
        n = n2
    return n


def sec_url(t: dict) -> str:
    acc = t["accession"]
    return (f"https://www.sec.gov/Archives/edgar/data/{t['issuer_cik']}/"
            f"{acc.replace('-', '')}/{acc}-index.htm")


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


# ---------- head extras ----------

def analytics_tag() -> str:
    gid = config().get("ga_measurement_id")
    if not gid:
        return ""
    gid = e(gid)
    return f"""<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','{gid}');
document.addEventListener('submit',function(ev){{var f=ev.target;if(f&&f.dataset&&f.dataset.track){{gtag('event',f.dataset.track);}}}});</script>"""


def head_tags(title: str, description: str, canonical: str, image: str | None,
              noindex: bool = False) -> str:
    cfg = config()
    img = image or f"{cfg['site_url']}/og/home.png"
    tags = [
        f'<link rel="canonical" href="{e(canonical)}">',
        f'<meta property="og:site_name" content="{e(cfg["site_name"])}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:description" content="{e(description)}">',
        f'<meta property="og:url" content="{e(canonical)}">',
        f'<meta property="og:image" content="{e(img)}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<link rel="alternate" type="application/rss+xml" title="{e(cfg["site_name"])}" href="/feed.xml">',
    ]
    if noindex:
        tags.append('<meta name="robots" content="noindex">')
    a = analytics_tag()
    if a:
        tags.append(a)
    return "\n".join(tags)


# ---------- calls to action ----------

def signup_block(context: str = "page") -> str:
    """Weekly email signup. Hidden until a Kit form is connected."""
    action = config().get("kit_form_action")
    if not action:
        return ""
    return f"""<section class="signup" aria-labelledby="signup-h-{context}">
  <h2 id="signup-h-{context}">The week's top insider buys, every Monday</h2>
  <p>Clusters, CEO purchases and the largest buys from the past week's SEC filings. One free email a week.</p>
  <form class="signup-form" action="{e(action)}" method="post" data-track="newsletter_signup">
    <label for="email-{context}">Email address</label>
    <div class="signup-row">
      <input id="email-{context}" type="email" name="email_address" required autocomplete="email" placeholder="you@example.com">
      <button type="submit">Subscribe</button>
    </div>
  </form>
  <p class="fine">Unsubscribe any time. No spam, no stock picks.</p>
</section>"""


def waitlist_block() -> str:
    action = config().get("kit_waitlist_form_action")
    if not action:
        return ""
    return f"""<section class="waitlist" aria-labelledby="waitlist-h">
  <h2 id="waitlist-h">Want to know the moment a CEO buys?</h2>
  <p>Instant email alerts for new clusters and large executive purchases are coming. Join the waitlist to hear first.</p>
  <form class="signup-form" action="{e(action)}" method="post" data-track="alerts_waitlist">
    <label for="email-waitlist">Email address</label>
    <div class="signup-row">
      <input id="email-waitlist" type="email" name="email_address" required autocomplete="email" placeholder="you@example.com">
      <button type="submit">Join the waitlist</button>
    </div>
  </form>
</section>"""


def promo_block() -> str:
    url = config().get("sheetwise_url")
    if not url:
        return ""
    link = f"{url}?utm_source=insidercluster&utm_medium=referral&utm_campaign=footer"
    return f"""<aside class="promo">
  <p><strong>Track your own portfolio and budget.</strong> Spreadsheet templates for investing and personal finance from
  <a href="{e(link)}" rel="noopener">SheetWise</a>, made by the person who built this site.</p>
</aside>"""


# ---------- page shell ----------

NAV = """<nav class="topnav" aria-label="Main">
    <a href="/today/">Today</a>
    <a href="/weekly/">Weekly</a>
    <a href="/stock/">All stocks</a>
  </nav>"""


def page(title: str, description: str, canonical: str, body: str, *, image: str | None = None,
         noindex: bool = False, extra_head: str = "") -> str:
    cfg = config()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
{head_tags(title, description, canonical, image, noindex)}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:ital,wght@0,400;0,500;0,600;0,800;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
{extra_head}
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="/">{e(cfg['site_name'])}</a>
  {NAV}
</div></header>
<main class="wrap">
{body}
{signup_block("footer") if not noindex else ""}
</main>
<footer><div class="wrap">
  {promo_block()}
  <p>Data comes from Form 4 filings on SEC EDGAR, updated every weekday. Open-market purchases and sales only; option exercises, grants and gifts are left out. <a href="/feed.xml">RSS feed</a>.</p>
  <p>This is information, not investment advice. Insiders buy and sell for many reasons, and a trade doesn't predict where the stock will go.</p>
</div></footer>
</body>
</html>
"""
