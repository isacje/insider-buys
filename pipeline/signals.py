"""Group transactions into trades and score which insider buys look meaningful.

Scoring is deliberately simple and explainable: every point comes with a
plain-English reason that the website shows next to the trade.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

ROLE_POINTS = {"CEO": 2, "CFO": 2, "President/COO": 1.5, "Chair": 1.5,
               "Officer": 1, "Director": 1, "10% owner": 0.5, "Other": 0}
CLUSTER_MIN_INSIDERS = 3
CLUSTER_WINDOW_DAYS = 30
PLAN_MIN_INSIDERS = 3          # same stock, same day, same price -> company plan
NO_TICKER = {"", "NONE", "N/A", "NA", "N.A.", "-", "--", "NULL", "NOT APPLICABLE"}
CLUSTER_MIN_VALUE = 50_000
# Stocks that genuinely trade above $1,000 a share. Others need corroboration.
HIGH_PRICE_OK = {"NVR", "BKNG", "AZO", "MTD", "FICO", "TPL", "MKL", "FCNCA",
                 "BRK.A", "BRK-A", "SEB", "WTM", "TDG", "MELI", "BLK", "GWW",
                 "MPWR", "KLAC", "LLY", "COST", "REGN", "NFLX", "ORLY", "CMG",
                 "DECK", "POWL", "CAR", "TYL", "URI", "ISRG", "MSCI", "NOW",
                 "INTU", "ASML", "AVGO", "META", "LII", "IT", "HUBB", "DPZ"}


def clean_ticker(raw: str) -> str:
    """'HEI, HEI.A' -> 'HEI'; placeholders for non-traded funds -> ''."""
    t = (raw or "").strip().upper()
    if t in NO_TICKER:
        return ""
    first = re.split(r"[,;/\s]+", t)[0]
    return "" if first in NO_TICKER else first


def group_trades(rows: list[dict]) -> list[dict]:
    """Combine a filing's open-market buys (or sells) into one trade.

    Rows for securities without a real trading symbol (private funds, BDCs)
    are dropped, and the same trade reported in two filings is kept once.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["code"] in ("P", "S"):
            ticker = clean_ticker(r["ticker"])
            if ticker:
                groups[(r["accession"], r["code"])].append({**r, "ticker": ticker})

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

    # The same trade can be filed twice: by one insider in two filings, or by
    # two related filers (a fund and its manager). Three or more identical
    # trades by different people are left alone; that's a plan purchase.
    seen, unique = set(), []
    for t in trades:
        key = (t["ticker"], t["insider_cik"], t["side"], t["trade_date"],
               t["shares"], t["avg_price"])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    twins: dict[tuple, list[dict]] = defaultdict(list)
    for t in unique:
        twins[(t["ticker"], t["side"], t["trade_date"], t["shares"], t["avg_price"])].append(t)
    out = []
    for t in unique:
        g = twins[(t["ticker"], t["side"], t["trade_date"], t["shares"], t["avg_price"])]
        if len(g) == 2:
            if t is g[1]:
                continue
            t["also_reported_by"] = g[1]["insider"]
        out.append(t)
    return out


def flag_bad_prices(trades: list[dict]) -> list[dict]:
    """Set aside trades whose price looks like a filing error.

    Filers sometimes type the total dollar amount into the price-per-share
    field, which turns a $2M purchase into a $10 trillion one. Without a
    market-price feed we compare each trade with other filings for the same
    stock, and treat huge or uncorroborated prices with suspicion.
    Returns the trades that were set aside.
    """
    prices: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for t in trades:
        if t["avg_price"]:
            prices[t["ticker"]].append((t["accession"], t["insider_cik"], t["avg_price"]))

    def corroborated(t: dict) -> bool:
        """At least two other insiders filed a similar price for this stock.
        (One isn't enough: two filers can make the same mistake.)"""
        p = t["avg_price"]
        return len({cik for acc, cik, q in prices[t["ticker"]]
                    if cik != t["insider_cik"] and p / 3 <= q <= p * 3}) >= 2

    bad = []
    for t in trades:
        p, v = t["avg_price"], t["value"] or 0
        others = [q for acc, _, q in prices[t["ticker"]] if acc != t["accession"]]
        far_from_typical = (len(others) >= 2 and p
                            and abs(math.log(p / median(others))) > math.log(5))
        # A $1,000+ price only counts as an error when it also implies an
        # absurd trade or stake (the usual signature of a total typed as price).
        stake = (t["owned_after"] or 0) * (p or 0)
        high_price = (p and p >= 1000 and t["ticker"] not in HIGH_PRICE_OK
                      and not corroborated(t) and (v >= 1e8 or stake >= 5e9))
        if far_from_typical or high_price or (v >= 3e9 and not corroborated(t)):
            t["data_issue"] = "Price per share looks wrong in the filing"
            bad.append(t)
    return bad


def flag_plan_purchases(buys: list[dict]) -> None:
    """Mark buys where several insiders bought the same stock on the same day
    at the same price, which is how company purchase plans and director-fee
    stock look, rather than individual decisions."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for b in buys:
        groups[(b["ticker"], b["trade_date"], b["avg_price"])].append(b)
    for g in groups.values():
        if len({b["insider_cik"] for b in g}) >= PLAN_MIN_INSIDERS:
            for b in g:
                b["plan_group"] = len({x["insider_cik"] for x in g})


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
        ts = [t for t in ts if not t.get("plan_group")]
        insiders = {}
        for t in ts:
            prev = insiders.get(t["insider_cik"])
            insiders[t["insider_cik"]] = {
                "name": t["insider"], "role": t["role"],
                "value": (prev["value"] if prev else 0) + t["value"],
            }
        if (len(insiders) < CLUSTER_MIN_INSIDERS
                or sum(i["value"] for i in insiders.values()) < CLUSTER_MIN_VALUE):
            continue
        dates = sorted(t["trade_date"] for t in ts)
        if dates[0] == dates[-1] and len({t["avg_price"] for t in ts}) == 1:
            continue  # one day, one price: a plan purchase, not a cluster
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
    if v >= 10_000_000:
        pts += 4; reasons.append(f"Very large purchase (${v/1e6:,.1f}M)")
    elif v >= 1_000_000:
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

    if t.get("plan_group"):
        pts -= 3; reasons.append(
            f"Same day and price as {t['plan_group'] - 1} other insiders (likely a company plan)")
    elif t["ticker"] in cluster_tickers:
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
    bad = {id(t) for t in flag_bad_prices(trades)}
    start = (as_of - timedelta(days=days)).isoformat()
    recent = [t for t in trades if t["filed"] >= start]
    issues = [t for t in recent if id(t) in bad]
    recent = [t for t in recent if id(t) not in bad]
    buys = [t for t in recent if t["side"] == "buy" and t["value"] > 0]
    sells = [t for t in recent if t["side"] == "sell" and t["value"] > 0]

    flag_plan_purchases(buys)
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
            "plan_purchases": sum(1 for t in buys if t.get("plan_group")),
            "set_aside_for_bad_prices": len(issues),
        },
        "data_issues": [
            {k: t[k] for k in ("ticker", "company", "insider", "side", "shares",
                               "avg_price", "accession", "issuer_cik", "filed")}
            for t in issues],
        "buys": buys[:1500],
        "clusters": clusters[:100],
        "top_sells": sells[:200],
    }
