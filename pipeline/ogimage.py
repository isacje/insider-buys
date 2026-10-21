"""1200x630 link-preview images, drawn with Pillow in the site's colors.

A preview image is what shows up when a page is shared on Bluesky, X,
Reddit, iMessage or Slack. Each image leads with the one fact worth seeing.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .layout import ROOT, config, fmt_day, money, nice_company, plural

W, H = 1200, 630
PAPER = (238, 242, 244)
INK = (27, 42, 58)
SOFT = (85, 101, 122)
BUY = (35, 112, 74)
RULE = (201, 211, 220)
FONTS = ROOT / "assets" / "fonts"
NBSP = "\u00a0"


def day(s: str) -> str:
    """'Oct 13' that never breaks across lines."""
    return fmt_day(s, False).replace(" ", NBSP)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, size: int, bold: bool, min_size: int = 28):
    """Largest font size (down to min_size) at which text fits; trims with … if needed."""
    while size > min_size and draw.textlength(text, font=font(size, bold)) > max_w:
        size -= 4
    f = font(size, bold)
    while draw.textlength(text, font=f) > max_w and len(text) > 4:
        text = text[:-2].rstrip() + "…"
    return text, f


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


def card(kicker: str, headline: str, line: str, marks: int = 0) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    pad = 72
    d.text((pad, 60), config()["site_name"], font=font(34, True), fill=INK)
    d.text((pad, 112), kicker, font=font(30), fill=SOFT)
    d.line((pad, 168, W - pad, 168), fill=INK, width=4)

    text, f = fit(d, headline, W - 2 * pad, 132, True, 52)
    d.text((pad, 196), text, font=f, fill=INK)
    y = 196 + f.size + 34

    if marks:
        n = min(marks, 24)
        for i in range(n):
            x = pad + i * 30
            d.rounded_rectangle((x, y, x + 16, y + 70), radius=3, fill=BUY)
        y += 96

    f2 = font(40)
    for ln in wrap(d, line, f2, W - 2 * pad, 3 if not marks else 2):
        d.text((pad, y), ln.replace(NBSP, " "), font=f2, fill=INK)
        y += 54

    d.text((pad, H - 64), "insidercluster.net  ·  from SEC Form 4 filings", font=font(26), fill=SOFT)
    return img


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)


def build_images(ds, site_dir: Path) -> set[str]:
    """Draw images for the home page, every stock with purchases, and each daily/weekly page.

    Returns keys of what was drawn: tickers, 'daily:YYYY-MM-DD', 'weekly:YYYY-Www'.
    """
    from .content import day_slice, notable_buys, role_phrase, week_label, week_slice, week_id

    out = site_dir / "og"
    if out.exists():
        shutil.rmtree(out)
    done: set[str] = set()

    save(card("Insider buying from SEC Form 4 filings", "Who's buying their own stock",
              "Executives and directors buying shares of their own companies, updated every weekday.", 5),
         out / "home.png")

    by_ticker: dict[str, list[dict]] = {}
    for b in ds.safe(ds.buys):
        by_ticker.setdefault(b["ticker"], []).append(b)
    for ticker, buys in by_ticker.items():
        company = nice_company(buys[0]["company"])
        c = ds.clusters.get(ticker)
        if c:
            line = (f"{c['insider_count']} insiders bought {money(c['total_value'])} "
                    f"between {day(c['first_date'])} and {day(c['last_date'])}.")
            marks = c["insider_count"]
        else:
            top = max(buys, key=lambda b: b["value"])
            line = (f"{top['insider']}, {role_phrase(top)}, bought {money(top['value'])} "
                    f"on {day(top['trade_date'])}.")
            marks = 0
        save(card(company, f"${ticker} insider buying", line, marks), out / "stock" / f"{ticker}.png")
        done.add(ticker)

    for d in ds.days:
        buys, sells, clusters = day_slice(ds, d)
        lead = notable_buys(buys)
        tickers = [c["ticker"] for c in clusters[:3]] or list(dict.fromkeys(b["ticker"] for b in lead))[:3]
        line = (f"{plural(len(buys), 'open-market purchase')} worth {money(sum(b['value'] for b in buys))}."
                + (f" Led by {', '.join('$' + t for t in tickers)}." if tickers else ""))
        save(card("Daily recap", f"Insider buys, {fmt_day(d, False)}", line), out / "daily" / f"{d}.png")
        done.add(f"daily:{d}")

    for wid in sorted({week_id(d) for d in ds.days}):
        _, buys, _, clusters = week_slice(ds, wid)
        tickers = [c["ticker"] for c in clusters[:3]]
        line = (f"{plural(len(buys), 'purchase')} worth {money(sum(b['value'] for b in buys))}."
                + (f" Cluster buying in {', '.join('$' + t for t in tickers)}." if tickers else ""))
        save(card("Weekly digest", "Top insider buys", f"{week_label(wid)}. {line}"), out / "weekly" / f"{wid}.png")
        done.add(f"weekly:{wid}")
    return done
