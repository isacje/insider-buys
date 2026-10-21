"""Daily recaps, weekly digests (web + email), share-ready social posts and RSS.

All of it is generated from the same cleaned Dataset as the stock pages, so
the numbers match everywhere on the site.
"""
from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from .layout import (config, e, fmt_day, money, nice_company, page, plural,
                     short_company)
from .pages import Dataset, clusters_as_of, stock_link, trade_table

STRENGTH_RANK = {"High": 3, "Medium": 2, "Low": 1}


# ---------- picking what matters ----------

def notable_buys(buys: list[dict]) -> list[dict]:
    """Buys worth showing first: not company-plan purchases, strongest and largest first."""
    keep = [b for b in buys if not b.get("plan_group")]
    return sorted(keep, key=lambda b: (STRENGTH_RANK[b["strength"]], b["value"]), reverse=True)


def day_slice(ds: Dataset, day: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Buys, sells and clusters touched by filings on one day."""
    buys = ds.safe([b for b in ds.buys if b["filed"] == day])
    sells = ds.safe([s for s in ds.sells if s["filed"] == day])
    cl = clusters_as_of(ds, day)
    touched = {b["ticker"] for b in buys if not b.get("plan_group")}
    clusters = sorted((c for t, c in cl.items() if t in touched),
                      key=lambda c: (c["insider_count"], c["total_value"]), reverse=True)
    return buys, sorted(sells, key=lambda s: s["value"], reverse=True), clusters


def week_id(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def week_range(wid: str) -> tuple[date, date]:
    y, w = wid.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    return monday, monday + timedelta(days=4)


def week_label(wid: str) -> str:
    mon, fri = week_range(wid)
    if mon.month == fri.month:
        return f"{mon:%b} {mon.day}–{fri.day}, {fri.year}"
    return f"{mon:%b} {mon.day} – {fri:%b} {fri.day}, {fri.year}"


def week_slice(ds: Dataset, wid: str):
    days = [d for d in ds.days if week_id(d) == wid]
    buys = ds.safe([b for b in ds.buys if b["filed"] in days])
    sells = ds.safe([s for s in ds.sells if s["filed"] in days])
    cl = clusters_as_of(ds, days[-1]) if days else {}
    touched = {b["ticker"] for b in buys if not b.get("plan_group")}
    clusters = sorted((c for t, c in cl.items() if t in touched),
                      key=lambda c: (c["insider_count"], c["total_value"]), reverse=True)
    return days, buys, sorted(sells, key=lambda s: s["value"], reverse=True), clusters


# ---------- sentences for posts and emails ----------

def role_phrase(t: dict) -> str:
    company = short_company(t["company"])
    role = t["role"]
    if role in ("CEO", "CFO", "Chair"):
        return f"{role} of {company}"
    if role == "President/COO":
        title = (t.get("title") or "President").split(",")[0].strip()
        return f"{title} of {company}"
    if role == "Director":
        return f"a director at {company}"
    if role == "Officer":
        title = (t.get("title") or "").split(",")[0].strip()
        return f"{title} at {company}" if title else f"an executive at {company}"
    if role == "10% owner":
        return f"a 10% owner of {company}"
    return f"an insider at {company}"


def buy_sentence(t: dict) -> str:
    s = (f"{t['insider']}, {role_phrase(t)} (${t['ticker']}), bought {money(t['value'])} "
         f"of stock on {fmt_day(t['trade_date'], year=False)}")
    if t.get("new_position"):
        s += ", opening a new position"
    elif (t.get("holding_change_pct") or 0) >= 100:
        s += ", more than doubling their stake"
    elif (t.get("holding_change_pct") or 0) >= 20:
        s += f", raising their stake {t['holding_change_pct']:.0f}%"
    return s + "."


def cluster_sentence(c: dict) -> str:
    return (f"{c['insider_count']} insiders at {short_company(c['company'])} (${c['ticker']}) have bought "
            f"{money(c['total_value'])} of stock since {fmt_day(c['first_date'], year=False)}, "
            "according to SEC Form 4 filings.")


def stories(ds: Dataset, day: str, limit: int = 3) -> list[dict]:
    """The day's most post-worthy items: clusters first, then the strongest buys."""
    buys, _, clusters = day_slice(ds, day)
    out, seen = [], set()
    for c in clusters:
        out.append({"kind": "cluster", "ticker": c["ticker"], "text": cluster_sentence(c),
                    "key": f"{day}:cluster:{c['ticker']}"})
        seen.add(c["ticker"])
    for b in notable_buys(buys):
        if b["strength"] == "Low" or b["ticker"] in seen:
            continue
        out.append({"kind": "buy", "ticker": b["ticker"], "text": buy_sentence(b),
                    "key": f"{day}:buy:{b['ticker']}:{b['insider_cik']}"})
        seen.add(b["ticker"])
    for s in out:
        s["url"] = f"{config()['site_url']}/stock/{s['ticker']}/"
    return out[:limit]


# ---------- daily pages ----------

def daily_body(ds: Dataset, day: str, prev_day: str | None, next_day: str | None) -> tuple[str, str]:
    buys, sells, clusters = day_slice(ds, day)
    notable = notable_buys(buys)
    lede = (f"Insiders reported {plural(len(buys), 'open-market purchase')} worth "
            f"<strong>{money(sum(b['value'] for b in buys))}</strong> and "
            f"{plural(len(sells), 'sale')} worth <strong>{money(sum(s['value'] for s in sells))}</strong> "
            f"in SEC filings on {fmt_day(day)}.")
    parts = [f"""<nav class="crumbs"><a href="/">Home</a> / <a href="/daily/">Daily recaps</a> / {fmt_day(day)}</nav>
<section class="stock-head">
  <h1>Insider buying on {fmt_day(day)}</h1>
  <p class="lede">{lede}</p>
</section>"""]
    if clusters:
        items = "".join(
            f'<li>{stock_link(c["ticker"])} <span>{e(nice_company(c["company"]))}: '
            f'{c["insider_count"]} insiders bought {money(c["total_value"])} between '
            f'{fmt_day(c["first_date"], False)} and {fmt_day(c["last_date"], False)}</span></li>'
            for c in clusters)
        parts.append(f'<section class="block"><h2>Cluster buying in today\'s filings</h2><ul class="story-list">{items}</ul></section>')
    shown = notable[:20]
    more = len(notable) - len(shown)
    parts.append(f"""<section class="block"><h2>Top purchases</h2>{trade_table(shown, "buy", show_stock=True)}
{f'<p class="more">Plus {plural(more, "smaller purchase")}. Each stock page has the full history.</p>' if more > 0 else ''}</section>""")
    parts.append(f'<section class="block"><h2>Largest sales</h2>{trade_table(sells[:10], "sell", show_stock=True)}</section>')
    nav = []
    if prev_day:
        nav.append(f'<a href="/daily/{prev_day}/">Previous day: {fmt_day(prev_day)}</a>')
    if next_day:
        nav.append(f'<a href="/daily/{next_day}/">Next day: {fmt_day(next_day)}</a>')
    if nav:
        parts.append(f'<nav class="pager">{" ".join(nav)}</nav>')
    desc = (f"Insider trading on {fmt_day(day)}: {plural(len(buys), 'open-market purchase')} "
            f"worth {money(sum(b['value'] for b in buys))}"
            + (f", led by {', '.join('$' + c['ticker'] for c in clusters[:3])}" if clusters else "")
            + ". From SEC Form 4 filings.")
    return "\n".join(parts), desc


# ---------- weekly digest ----------

def weekly_body(ds: Dataset, wid: str) -> tuple[str, str]:
    days, buys, sells, clusters = week_slice(ds, wid)
    notable = notable_buys(buys)
    execs = [b for b in notable if b["role"] in ("CEO", "CFO")][:10]
    label = week_label(wid)
    lede = (f"In SEC filings for {label}, insiders reported {plural(len(buys), 'open-market purchase')} worth "
            f"<strong>{money(sum(b['value'] for b in buys))}</strong> and {plural(len(sells), 'sale')} worth "
            f"<strong>{money(sum(s['value'] for s in sells))}</strong>.")
    parts = [f"""<nav class="crumbs"><a href="/">Home</a> / <a href="/weekly/">Weekly digests</a> / {label}</nav>
<section class="stock-head">
  <h1>Top insider buys: {label}</h1>
  <p class="lede">{lede}</p>
</section>"""]
    if clusters:
        items = "".join(
            f'<li>{stock_link(c["ticker"])} <span>{e(nice_company(c["company"]))}: '
            f'{c["insider_count"]} insiders bought {money(c["total_value"])} between '
            f'{fmt_day(c["first_date"], False)} and {fmt_day(c["last_date"], False)}</span></li>'
            for c in clusters[:10])
        parts.append(f'<section class="block"><h2>Cluster buying</h2><ul class="story-list">{items}</ul></section>')
    parts.append(f'<section class="block"><h2>Top 10 purchases</h2>{trade_table(notable[:10], "buy", show_stock=True)}</section>')
    if execs:
        parts.append(f'<section class="block"><h2>CEO and CFO purchases</h2>{trade_table(execs, "buy", show_stock=True)}</section>')
    parts.append(f'<section class="block"><h2>Largest sales</h2>{trade_table(sells[:5], "sell", show_stock=True)}</section>')
    desc = (f"The week's top insider buys for {label}: {plural(len(buys), 'purchase')} worth "
            f"{money(sum(b['value'] for b in buys))}"
            + (f", with cluster buying in {', '.join('$' + c['ticker'] for c in clusters[:3])}" if clusters else "")
            + ". From SEC Form 4 filings.")
    return "\n".join(parts), desc


def weekly_email(ds: Dataset, wid: str) -> tuple[str, str]:
    """Email-safe HTML (inline styles, no external CSS) and a subject line."""
    site = config()["site_url"]
    utm = f"utm_source=newsletter&utm_medium=email&utm_campaign={wid}"
    days, buys, sells, clusters = week_slice(ds, wid)
    notable = notable_buys(buys)
    label = week_label(wid)
    a = 'style="color:#2F5D8C;font-weight:600;text-decoration:none"'
    p = 'style="margin:0 0 14px;font-size:16px;line-height:1.5;color:#1B2A3A"'
    h2 = 'style="margin:28px 0 10px;font-size:18px;color:#1B2A3A"'
    li = 'style="margin:0 0 10px;font-size:15px;line-height:1.45;color:#1B2A3A"'

    def link(t: str) -> str:
        return f'<a {a} href="{site}/stock/{t}/?{utm}">${e(t)}</a>'

    parts = [f'<p {p}>Here are the insider purchases worth knowing about from SEC filings for {label}: '
             f'{plural(len(buys), "open-market purchase")} worth {money(sum(b["value"] for b in buys))} in total.</p>']
    if clusters:
        parts.append(f'<h2 {h2}>Cluster buying</h2><ul style="padding-left:20px;margin:0">' + "".join(
            f'<li {li}>{link(c["ticker"])} {e(short_company(c["company"]))}: {c["insider_count"]} insiders '
            f'bought {money(c["total_value"])} since {fmt_day(c["first_date"], False)}</li>' for c in clusters[:5]) + "</ul>")
    parts.append(f'<h2 {h2}>Top purchases</h2><ol style="padding-left:20px;margin:0">' + "".join(
        f'<li {li}>{link(b["ticker"])} {e(b["insider"])}, {e(role_phrase(b))}, bought '
        f'<strong>{money(b["value"])}</strong> on {fmt_day(b["trade_date"], False)}'
        f'{" (new position)" if b.get("new_position") else ""}</li>' for b in notable[:10]) + "</ol>")
    if sells:
        parts.append(f'<h2 {h2}>Largest sales</h2><ul style="padding-left:20px;margin:0">' + "".join(
            f'<li {li}>{link(s["ticker"])} {e(s["insider"])} sold {money(s["value"])}'
            f'{" (pre-scheduled plan)" if s["plan_10b5_1"] else ""}</li>' for s in sells[:3]) + "</ul>")
    parts.append(f'<p style="margin:24px 0 14px;font-size:16px;line-height:1.5"><a {a} href="{site}/weekly/{wid}/?{utm}">See the full digest with every trade</a></p>')
    parts.append('<p style="margin:24px 0 0;font-size:12px;line-height:1.5;color:#55657A">From SEC Form 4 filings. '
                 'This is information, not investment advice. Insiders buy and sell for many reasons.</p>')
    html = ('<div style="max-width:600px;margin:0 auto;font-family:Helvetica,Arial,sans-serif">'
            + "".join(parts) + "</div>")
    biggest_cluster = max(clusters, key=lambda c: c["total_value"])["ticker"] if clusters else None
    picks = list(dict.fromkeys(([biggest_cluster] if biggest_cluster else []) + [b["ticker"] for b in notable[:4]]))[:3]
    subject = f"Top insider buys, {label}" + (": " + ", ".join(f"${t}" for t in picks) if picks else "")
    return html, subject


def email_page(ds: Dataset, wid: str) -> str:
    html, subject = weekly_email(ds, wid)
    body = f"""<section class="stock-head">
  <h1>Email for {week_label(wid)}</h1>
  <p class="lede">Copy this into a new Kit broadcast. The subject line and the email body each have a copy button.</p>
</section>
<div class="copybar">
  <p><strong>Subject:</strong> <span id="subject">{e(subject)}</span></p>
  <button type="button" data-copy-text="#subject">Copy subject</button>
  <button type="button" data-copy-html="#email">Copy email</button>
</div>
<div id="email" class="email-preview">{html}</div>
{COPY_SCRIPT}"""
    return page(f"Email: {week_label(wid)}", "Newsletter draft", f"{config()['site_url']}/weekly/{wid}/email/",
                body, noindex=True)


# ---------- share-ready posts ----------

COPY_SCRIPT = """<script>
document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('button[data-copy-text],button[data-copy-html]');
  if (!b) return;
  const el = document.querySelector(b.dataset.copyText || b.dataset.copyHtml);
  try {
    if (b.dataset.copyHtml && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([el.innerHTML], {type: 'text/html'}),
        'text/plain': new Blob([el.innerText], {type: 'text/plain'})})]);
    } else {
      await navigator.clipboard.writeText(el.value !== undefined ? el.value : el.innerText);
    }
    const t = b.textContent; b.textContent = 'Copied'; setTimeout(() => b.textContent = t, 1500);
  } catch (err) { b.textContent = 'Select and copy manually'; }
});
</script>"""


def x_post(story: dict) -> str:
    """X counts any link as 23 characters; keep the whole post under 280."""
    text = story["text"]
    if len(text) + 2 + 23 > 280:
        text = text[: 280 - 2 - 23 - 1].rsplit(" ", 1)[0] + "…"
    return f"{text}\n\n{story['url']}"


def bluesky_text(story: dict) -> tuple[str, str]:
    """Post text (<=300 characters) and the visible link text inside it."""
    shown = story["url"].split("://", 1)[1]
    text = story["text"]
    if len(text) + 2 + len(shown) > 300:
        text = text[: 300 - 2 - len(shown) - 1].rsplit(" ", 1)[0] + "…"
    return f"{text}\n\n{shown}", shown


def reddit_post(ds: Dataset, day: str) -> tuple[str, str]:
    buys, sells, clusters = day_slice(ds, day)
    notable = notable_buys(buys)[:8]
    site = config()["site_url"]
    title = (f"Insider buying roundup for {fmt_day(day)}: "
             + (", ".join(f"${c['ticker']}" for c in clusters[:3]) + " see cluster buying" if clusters
                else f"{plural(len(buys), 'open-market purchase')} from SEC Form 4s"))
    lines = [f"Open-market insider purchases reported to the SEC on {fmt_day(day)} "
             f"({plural(len(buys), 'purchase')}, {money(sum(b['value'] for b in buys))} total). "
             "Grants, option exercises and company-plan purchases are excluded.", ""]
    if clusters:
        lines += ["**Cluster buying (3+ insiders in 30 days)**", ""]
        lines += [f"- ${c['ticker']} ({short_company(c['company'])}): {c['insider_count']} insiders, "
                  f"{money(c['total_value'])} since {fmt_day(c['first_date'], False)}" for c in clusters[:5]]
        lines.append("")
    lines += ["**Largest notable purchases**", "", "| Ticker | Insider | Role | Value | Holdings change |",
              "|---|---|---|---|---|"]
    for b in notable:
        chg = "new position" if b.get("new_position") else (
            f"+{b['holding_change_pct']}%" if b.get("holding_change_pct") is not None else "–")
        lines.append(f"| ${b['ticker']} | {b['insider']} | {b['title'] or b['role']} | {money(b['value'])} | {chg} |")
    lines += ["", f"Full list with links to each filing: {site}/daily/{day}/", "",
              "Disclosure: I built this free tracker from SEC EDGAR data. Not investment advice."]
    return title, "\n".join(lines)


def share_page(ds: Dataset, day: str) -> str:
    items = stories(ds, day, limit=3)
    blocks = []
    for i, s in enumerate(items):
        bsky, _ = bluesky_text(s)
        stocktwits = f"{s['text']} Full list: {s['url']}"
        blocks.append(f"""<section class="block share">
  <h2>{i + 1}. {e(s['ticker'])} ({'cluster' if s['kind'] == 'cluster' else 'purchase'})</h2>
  <label>X</label><textarea id="x{i}" rows="4">{e(x_post(s))}</textarea><button type="button" data-copy-text="#x{i}">Copy</button>
  <label>StockTwits</label><textarea id="st{i}" rows="3">{e(stocktwits)}</textarea><button type="button" data-copy-text="#st{i}">Copy</button>
  <label>Bluesky (posted automatically when connected)</label><textarea id="bs{i}" rows="4">{e(bsky)}</textarea><button type="button" data-copy-text="#bs{i}">Copy</button>
</section>""")
    title, body = reddit_post(ds, day)
    blocks.append(f"""<section class="block share">
  <h2>Reddit post</h2>
  <p class="more">Good fits: r/stocks, r/ValueInvesting, r/pennystocks (small caps). Read each subreddit's self-promotion rules first and post at most once a week per subreddit.</p>
  <label>Title</label><textarea id="rt" rows="2">{e(title)}</textarea><button type="button" data-copy-text="#rt">Copy title</button>
  <label>Body (Markdown)</label><textarea id="rb" rows="16">{e(body)}</textarea><button type="button" data-copy-text="#rb">Copy body</button>
</section>""")
    empty = "" if items else '<div class="empty">No standout purchases in the latest filings. Check tomorrow.</div>'
    html = f"""<section class="stock-head">
  <h1>Posts for {fmt_day(day)}</h1>
  <p class="lede">Ready-to-paste posts about the latest filings. This page isn't shown to search engines.</p>
</section>{empty}{''.join(blocks)}{COPY_SCRIPT}"""
    return page(f"Share: {fmt_day(day)}", "Share-ready posts", f"{config()['site_url']}/share/", html, noindex=True)


# ---------- index pages, RSS ----------

def list_page(title: str, h1: str, intro: str, links: list[tuple[str, str, str]], canonical: str) -> str:
    items = "".join(f'<li><a href="{e(href)}">{e(label)}</a> <span>{e(note)}</span></li>'
                    for href, label, note in links)
    body = f"""<section class="stock-head"><h1>{e(h1)}</h1><p class="lede">{e(intro)}</p></section>
<ul class="story-list">{items}</ul>"""
    return page(title, intro, canonical, body)


def rss(items: list[dict]) -> str:
    cfg = config()
    out = []
    for it in items[:40]:
        when = datetime.fromisoformat(it["date"]).replace(hour=12, tzinfo=timezone.utc)
        out.append(f"""<item><title>{e(it['title'])}</title><link>{e(it['url'])}</link>
<guid isPermaLink="true">{e(it['url'])}</guid><pubDate>{format_datetime(when)}</pubDate>
<description>{e(it['description'])}</description></item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>{e(cfg['site_name'])}</title><link>{cfg['site_url']}/</link>
<description>Daily and weekly insider buying from SEC Form 4 filings.</description>
<language>en-us</language>
{''.join(out)}
</channel></rss>
"""


def build_content(ds: Dataset, site_dir: Path, images: set[str]) -> dict:
    """Write daily, weekly, share and RSS pages. Returns URLs for the sitemap."""
    site = config()["site_url"]
    for sub in ("daily", "weekly", "today", "share"):
        if (site_dir / sub).exists():
            shutil.rmtree(site_dir / sub)
    urls, feed = [], []
    days = ds.days
    if not days:
        return {"urls": urls, "latest_day": None, "latest_week": None}

    for i, day in enumerate(days):
        body, desc = daily_body(ds, day, days[i - 1] if i else None, days[i + 1] if i + 1 < len(days) else None)
        img = f"{site}/og/daily/{day}.png" if f"daily:{day}" in images else None
        d = site_dir / "daily" / day
        d.mkdir(parents=True)
        (d / "index.html").write_text(page(f"Insider buys on {fmt_day(day)}: top purchases from SEC filings",
                                           desc, f"{site}/daily/{day}/", body, image=img))
        urls.append(f"{site}/daily/{day}/")
        feed.append({"date": day, "title": f"Insider buying on {fmt_day(day)}",
                     "url": f"{site}/daily/{day}/", "description": desc})
    latest = days[-1]
    body, desc = daily_body(ds, latest, days[-2] if len(days) > 1 else None, None)
    img = f"{site}/og/daily/{latest}.png" if f"daily:{latest}" in images else None
    (site_dir / "today").mkdir(parents=True)
    (site_dir / "today" / "index.html").write_text(
        page("Insider buying today: the latest SEC Form 4 purchases", desc, f"{site}/today/", body, image=img))
    urls.append(f"{site}/today/")
    (site_dir / "daily" / "index.html").write_text(list_page(
        "Daily insider buying recaps", "Daily insider buying recaps",
        "Every filing day's open-market insider purchases and sales.",
        [(f"/daily/{d}/", fmt_day(d), "") for d in reversed(days)], f"{site}/daily/"))
    urls.append(f"{site}/daily/")

    weeks = sorted({week_id(d) for d in days})
    week_links = []
    for wid in weeks:
        body, desc = weekly_body(ds, wid)
        img = f"{site}/og/weekly/{wid}.png" if f"weekly:{wid}" in images else None
        d = site_dir / "weekly" / wid
        d.mkdir(parents=True)
        (d / "index.html").write_text(page(f"Top insider buys this week: {week_label(wid)}", desc,
                                           f"{site}/weekly/{wid}/", body, image=img))
        (d / "email").mkdir()
        (d / "email" / "index.html").write_text(email_page(ds, wid))
        urls.append(f"{site}/weekly/{wid}/")
        week_links.append((f"/weekly/{wid}/", week_label(wid), desc.split(": ", 1)[-1]))
        feed.append({"date": week_range(wid)[1].isoformat(), "title": f"Top insider buys: {week_label(wid)}",
                     "url": f"{site}/weekly/{wid}/", "description": desc})
    (site_dir / "weekly" / "index.html").write_text(list_page(
        "Weekly insider buying digests", "Weekly insider buying digests",
        "The week's clusters, CEO and CFO purchases and largest buys, from SEC Form 4 filings.",
        list(reversed(week_links)), f"{site}/weekly/"))
    urls.append(f"{site}/weekly/")

    (site_dir / "share").mkdir(parents=True)
    (site_dir / "share" / "index.html").write_text(share_page(ds, latest))
    feed.sort(key=lambda it: it["date"], reverse=True)
    (site_dir / "feed.xml").write_text(rss(feed))
    return {"urls": urls, "latest_day": latest, "latest_week": weeks[-1]}
