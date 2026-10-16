"""Generate sample Form 4 XML files for fictional companies.

Used for tests and the offline demo. Every company and person here is made up;
real data comes from SEC EDGAR when the pipeline runs on GitHub.

  python tests/make_sample.py tests/fixtures 2026-10-15
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

COMPANIES = [
    ("Northwind Robotics Inc", "NWRB", 41.20),
    ("Halcyon Biotherapeutics", "HLCB", 8.75),
    ("Granite Peak Bancorp", "GPKB", 27.60),
    ("Lumen Grid Energy Corp", "LGEN", 63.10),
    ("Cobalt Harbor Logistics", "CBHL", 15.40),
    ("Meridian Foods Co", "MRDF", 52.30),
    ("Starling Semiconductor", "STSM", 118.90),
    ("Brightwater Utilities", "BWUT", 34.80),
]
FIRST = ["Alex", "Jordan", "Priya", "Marcus", "Elena", "Tom", "Grace", "Omar",
         "Hannah", "Victor", "Mei", "Daniel", "Sofia", "Raj", "Claire", "Ben"]
LAST = ["Holloway", "Castillo", "Reyes", "Whitfield", "Okafor", "Lindqvist",
        "Brennan", "Sato", "Delgado", "Fairbanks", "Kowalski", "Mercer"]
ROLES = [("Chief Executive Officer", True, False), ("Chief Financial Officer", True, False),
         ("President", True, False), ("EVP, Operations", True, False),
         ("", False, True), ("", False, True), ("", False, True)]

TEMPLATE = """<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>{date}</periodOfReport>
  <issuer>
    <issuerCik>{icik}</issuerCik>
    <issuerName>{company}</issuerName>
    <issuerTradingSymbol>{ticker}</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>{ocik}</rptOwnerCik>
      <rptOwnerName>{name}</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>{is_dir}</isDirector>
      <isOfficer>{is_off}</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>{title}</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>{plan}</aff10b5One>
  <nonDerivativeTable>{txs}
  </nonDerivativeTable>
</ownershipDocument>
"""
TX = """
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>{date}</value></transactionDate>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>{code}</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>{di}</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>"""


def make(out: Path, as_of: date, seed: int = 7) -> int:
    rnd = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    people = {}  # (ticker, slot) -> person, so the same insiders recur
    n = 0
    for ci, (company, ticker, base) in enumerate(COMPANIES):
        # A few companies get clusters of buying; others mostly selling
        k = {0: 5, 1: 4, 2: 3}.get(ci, rnd.choice([1, 2]))
        for j in range(k + rnd.randint(1, 3)):
            slot = j % 6
            if (ticker, slot) not in people:
                title, off, dr = ROLES[slot]
                people[(ticker, slot)] = (
                    f"{rnd.choice(LAST)} {rnd.choice(FIRST)}", title, off, dr,
                    str(1_900_000 + ci * 100 + slot))
            name, title, off, dr, ocik = people[(ticker, slot)]
            buying = j < k if ci < 3 else rnd.random() < 0.3
            code, ad = ("P", "A") if buying else ("S", "D")
            day = as_of - timedelta(days=rnd.randint(1, 26))
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            held = rnd.randint(5_000, 400_000)
            lots, txs = rnd.randint(1, 3), ""
            for _ in range(lots):
                shares = rnd.choice([500, 1_000, 2_500, 5_000, 10_000, 25_000, 60_000])
                price = round(base * rnd.uniform(0.94, 1.06), 2)
                held = held + shares if buying else max(held - shares, 0)
                txs += TX.format(date=day, code=code, shares=shares, price=price,
                                 ad=ad, after=held, di=rnd.choice("DDDDI"))
            plan = "0" if buying else rnd.choice("011")
            acc = f"0009999999-26-{n:06d}"
            xml = TEMPLATE.format(date=day, icik=str(8_000_000 + ci), company=company,
                                  ticker=ticker, ocik=ocik, name=name.upper(),
                                  is_dir=int(dr), is_off=int(off), title=title,
                                  plan=plan, txs=txs)
            filed = day + timedelta(days=rnd.choice([1, 1, 2]))
            (out / f"{acc}.xml").write_text(xml)
            (out / f"{acc}.json").write_text(json.dumps(
                {"accession": acc, "filed": min(filed, as_of).isoformat()}))
            n += 1
    return n


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures")
    as_of = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    print(f"Wrote {make(folder, as_of)} sample filings to {folder}")
