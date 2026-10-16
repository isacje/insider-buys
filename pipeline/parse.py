"""Turn Form 4 XML into flat transaction rows."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

CODE_LABELS = {
    "P": "Buy",            # open-market or private purchase
    "S": "Sell",           # open-market or private sale
    "A": "Grant",
    "M": "Option exercise",
    "F": "Tax withholding",
    "G": "Gift",
}


def _text(el: ET.Element | None, path: str) -> str:
    """Read a field that may be <x>val</x> or <x><value>val</value></x>."""
    if el is None:
        return ""
    node = el.find(path)
    if node is None:
        return ""
    inner = node.find("value")
    raw = inner.text if inner is not None else node.text
    return (raw or "").strip()


_ENTITY = re.compile(
    r"\b(inc|llc|l\.?p|ltd|corp|co|fund|trust|capital|partners|holdings|group|"
    r"management|advisors|foundation|bank|plc|n\.?a)\b\.?", re.I)


def display_name(sec_name: str) -> str:
    """SEC lists people as 'LAST FIRST MIDDLE'; show them as 'First Middle Last'.

    Companies and funds (e.g. 'BERKSHIRE HATHAWAY INC') are left in order.
    """
    name = " ".join(sec_name.split())
    if not name or _ENTITY.search(name) or "," in name:
        if "," in name and not _ENTITY.search(name):  # 'LAST, FIRST'
            last, first = [p.strip() for p in name.split(",", 1)]
            name = f"{first} {last}"
        return name.title() if name.isupper() else name
    parts = name.split(" ")
    suffix = []
    while len(parts) > 2 and parts[-1].upper().strip(".") in {"JR", "SR", "II", "III", "IV"}:
        suffix.insert(0, parts.pop())
    ordered = parts[1:] + parts[:1] + suffix
    out = " ".join(ordered)
    return out.title() if out.isupper() else out


def _flag(el: ET.Element | None, path: str) -> bool:
    return _text(el, path).lower() in {"1", "true"}


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "")) if s else None
    except ValueError:
        return None


def role_of(rel: ET.Element | None) -> dict:
    title = _text(rel, "officerTitle")
    is_dir = _flag(rel, "isDirector")
    is_off = _flag(rel, "isOfficer")
    is_ten = _flag(rel, "isTenPercentOwner")
    t = title.lower()
    if re.search(r"\bceo\b|chief executive", t):
        label = "CEO"
    elif re.search(r"\bcfo\b|chief financial", t):
        label = "CFO"
    elif re.search(r"\bpresident\b|\bcoo\b|chief operating", t):
        label = "President/COO"
    elif re.search(r"chair", t):
        label = "Chair"
    elif is_off:
        label = "Officer"
    elif is_dir:
        label = "Director"
    elif is_ten:
        label = "10% owner"
    else:
        label = "Other"
    return {
        "role": label,
        "title": title,
        "is_director": is_dir,
        "is_officer": is_off,
        "is_ten_pct": is_ten,
    }


def parse_form4(xml: str, accession: str = "", filed: str = "") -> list[dict]:
    """Return one row per non-derivative transaction in the filing."""
    root = ET.fromstring(xml)
    issuer = root.find("issuer")
    ticker = _text(issuer, "issuerTradingSymbol").upper()
    company = _text(issuer, "issuerName")
    issuer_cik = _text(issuer, "issuerCik").lstrip("0")

    owners = root.findall("reportingOwner")
    if not owners:
        return []
    primary = owners[0]
    owner_name = _text(primary, "reportingOwnerId/rptOwnerName")
    owner_cik = _text(primary, "reportingOwnerId/rptOwnerCik").lstrip("0")
    role = role_of(primary.find("reportingOwnerRelationship"))
    # 10b5-1 checkbox exists on filings since April 2023
    plan_10b5_1 = _flag(root, "aff10b5One")

    rows = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode").upper()
        shares = _num(_text(tx, "transactionAmounts/transactionShares"))
        price = _num(_text(tx, "transactionAmounts/transactionPricePerShare"))
        rows.append({
            "accession": accession,
            "filed": filed,
            "ticker": ticker,
            "company": company,
            "issuer_cik": issuer_cik,
            "insider": display_name(owner_name),
            "insider_cik": owner_cik,
            "joint_filers": len(owners) - 1,
            **role,
            "plan_10b5_1": plan_10b5_1,
            "security": _text(tx, "securityTitle"),
            "date": _text(tx, "transactionDate")[:10],
            "code": code,
            "type": CODE_LABELS.get(code, code),
            "acq_disp": _text(tx, "transactionAmounts/transactionAcquiredDisposedCode"),
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2) if shares and price else None,
            "owned_after": _num(
                _text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")
            ),
            "direct": _text(tx, "ownershipNature/directOrIndirectOwnership") != "I",
        })
    return rows
