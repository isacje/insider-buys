"""Build the whole site from saved filing days."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .content import build_content
from .home import build_home
from .layout import config, e
from .ogimage import build_images
from .pages import build_stock_pages, prepare
from .signals import build_site_data


def build_site(rows_home: list[dict], rows_pages: list[dict], as_of: date, site_dir: Path,
               window: int = 30, page_window: int = 90) -> dict:
    cfg = config()
    site = cfg["site_url"]
    (site_dir / "data").mkdir(parents=True, exist_ok=True)

    data = build_site_data(rows_home, as_of, window)
    (site_dir / "data" / "insiders.json").write_text(json.dumps(data, separators=(",", ":")))

    ds = prepare(rows_pages, as_of, page_window)
    images = build_images(ds, site_dir)
    tickers = build_stock_pages(ds, site_dir, images)
    content = build_content(ds, site_dir, images)
    build_home(data, site_dir)

    urls = [f"{site}/", f"{site}/stock/"] + [f"{site}/stock/{t}/" for t in tickers] + content["urls"]
    (site_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{e(u)}</loc><lastmod>{as_of.isoformat()}</lastmod></url>\n" for u in urls)
        + "</urlset>\n")
    (site_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: /share/\n\nSitemap: {site}/sitemap.xml\n")
    (site_dir / "CNAME").write_text(site.split("://", 1)[-1] + "\n")
    if cfg.get("indexnow_key"):
        (site_dir / f"{cfg['indexnow_key']}.txt").write_text(cfg["indexnow_key"])

    # Pages worth pinging search engines about after this build
    latest = content["latest_day"]
    changed = [f"{site}/", f"{site}/today/"]
    if latest:
        changed.append(f"{site}/daily/{latest}/")
        changed.append(f"{site}/weekly/{content['latest_week']}/")
        changed += sorted({f"{site}/stock/{t['ticker']}/" for t in ds.safe(ds.buys + ds.sells)
                           if t["filed"] == latest})
    (site_dir / "data" / "changed.json").write_text(json.dumps(changed))

    s = data["stats"]
    return {"buys": s["buy_count"], "clusters": s["clusters"], "stock_pages": len(tickers),
            "images": len(images) + 1, "daily_pages": len(ds.days), "sitemap_urls": len(urls),
            "latest_day": latest, "changed": len(changed)}
