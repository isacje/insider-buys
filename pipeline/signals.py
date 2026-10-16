"""Group transactions into trades and score which insider buys look meaningful.

Scoring is deliberately simple and explainable: every point comes with a
plain-English reason that the website shows next to the trade.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

ROLE_POINTS = {"CEO": 2, "CFO": 2, "President/COO": 1.5, "Chair": 1.5,
               "Officer": 1, "Director": 1, "10% owner": 0.5, "Other": 0}
CLUSTER_MIN_INSIDERS = 3
CLUSTER_WINDOW_DAYS = 30


def group_trades(rows: list[dict]) -> list[dict]:
    """Combine a filing's open-market buys (or sells) into one trade."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["code"] in ("P", "S"):
            groups[(r["accession"], r["code"])].append(r)

    trades = []
    for (_, code), txs in groups.items():
        first = txs[0]
        shares = sum(t["shares"] or 0 for t in txs)
        value = sum(t["value"] or 0 for t in txs)
        dates = sorted(t["date"] for t in txs if t["date"])
        owned_after = txs[-1]["owned_after"]
        prior = None
        if owned_after is not None:
            prior = owned_after - shares if code == "P" else owned_after + shares
        pct = (shares / prior) if prior and prior > 0 else None
        trades.append({
            "accession": first["accession"],
            "filed": first["filed"],
            "trade_date": dates[0] if dates else first["filed"],
            "ticker": first["ticker"],
            "company": first["company"],
            "issuer_cik": first["issuer_cik"],
            "insider": first["insider"],
            "insider_cik": first["insider_cik"],
            "role": first["role"],
            "title": first["title"],
            "side": "buy" if code == "P" else "sell",
            "shares": round(shares),
            "avg_price": round(value / shares, 2) if shares and value else None,
            "value": round(value),
            "owned_after": round(owned_after) if owned_after is not None else None,
            "holding_change_pct": round(pct * 100, 1) if pct is not None else None,
            "new_position": prior == 0 if prior is not None else False,
            "plan_10b5_1": first["plan_10b5_1"],
            "direct": all(t["direct"] for t in txs),
            "late_days": _days_between(dates[-1] if dates else None, first["filed"]),
        })
    return trades


def _days_between(a: str | None, b: str) -> int | None:
    if not a:
        return None
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except ValueError:
        return None


def find_clusters(buys: list[dict], as_of: date) -> list[dict]:
    """Stocks where several different insiders bought within the window."""
    start = (as_of - timedelta(days=CLUSTER_WINDOW_DAYS)).isoformat()
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for t in buys:
        if t["trade_date"] >= start and t["ticker"]:
            by_ticker[t["ticker"]].append(t)

    clusters = []
    for ticker, ts in by_ticker.items():
        insiders = {}
        for t in ts:
            prev = insiders.get(t["insider_cik"])
            insiders[t["insider_cik"]] = {
                "name": t["insider"], "role": t["role"],
                "value": (prev["value"] if prev else 0) + t["value"],
            }
        if len(insiders) < CLUSTER_MIN_INSIDERS:
            continue
        dates = sorted(t["trade_date"] for t in ts)
        clusters.append({
            "ticker": ticker,
            "company": ts[0]["company"],
            "insider_count": len(insiders),
            "total_value": round(sum(i["value"] for i in insiders.values())),
            "first_date": dates[0],
            "last_date": dates[-1],
            "insiders": sorted(insiders.values(), key=lambda i: -i["value"]),
        })
    return sorted(clusters, key=lambda c: (-c["insider_count"], -c["total_value"]))


def score_buy(t: dict, cluster_tickers: set[str]) -> None:
    """Add `score`, `strength` and human-readable `reasons` to a buy."""
    pts, reasons = 0.0, []
    v = t["value"] or 0
    if v >= 1_000_000:
        pts += 3; reasons.append(f"Large purchase (${v/1e6:,.1f}M)")
    elif v >= 250_000:
        pts += 2; reasons.append(f"Sizable purchase (${v/1e3:,.0f}K)")
    elif v >= 50_000:
        pts += 1

    rp = ROLE_POINTS.get(t["role"], 0)
    if rp >= 1.5:
        reasons.append(f"Bought by the {t['role']}")
    pts += rp

    if t["new_position"]:
        pts += 2; reasons.append("Opens a new position")
    elif t["holding_change_pct"] is not None:
        if t["holding_change_pct"] >= 100:
            pts += 2; reasons.append(f"More than doubles holdings (+{t['holding_change_pct']:.0f}%)")
        elif t["holding_change_pct"] >= 20:
            pts += 1; reasons.append(f"Raises holdings {t['holding_change_pct']:.0f}%")

    if t["ticker"] in cluster_tickers:
        pts += 2; reasons.append("Other insiders buying too")
    if t["plan_10b5_1"]:
        pts -= 1; reasons.append("Pre-scheduled (10b5-1 plan)")
    if not t["direct"]:
        reasons.append("Held indirectly (trust, fund or family)")

    t["score"] = round(pts, 1)
    t["strength"] = "High" if pts >= 6 else "Medium" if pts >= 4 else "Low"
    t["reasons"] = reasons


def build_site_data(rows: list[dict], as_of: date, days: int = 30) -> dict:
    trades = group_trades(rows)
    start = (as_of - timedelta(days=days)).isoformat()
    recent = [t for t in trades if t["filed"] >= start]
    buys = [t for t in recent if t["side"] == "buy" and t["value"] > 0]
    sells = [t for t in recent if t["side"] == "sell" and t["value"] > 0]

    clusters = find_clusters(buys, as_of)
    cluster_tickers = {c["ticker"] for c in clusters}
    for b in buys:
        score_buy(b, cluster_tickers)

    buys.sort(key=lambda t: (t["filed"], t["score"]), reverse=True)
    sells.sort(key=lambda t: t["value"], reverse=True)

    return {
        "generated_at": as_of.isoformat(),
        "window_days": days,
        "stats": {
            "buy_count": len(buys),
            "buy_value": sum(t["value"] for t in buys),
            "sell_count": len(sells),
            "sell_value": sum(t["value"] for t in sells),
            "high_signal_buys": sum(1 for t in buys if t["strength"] == "High"),
            "clusters": len(clusters),
        },
        "buys": buys[:1500],
        "clusters": clusters[:100],
        "top_sells": sells[:200],
    }
