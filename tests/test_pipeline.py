from datetime import date

from pipeline.edgar import extract_xml, parse_index
from pipeline.parse import parse_form4
from pipeline.signals import build_site_data, group_trades

INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    Oct 15, 2026

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-Q        NORTHWIND ROBOTICS INC                                        8000000     20261015    edgar/data/8000000/0008000000-26-000010.txt
4           NORTHWIND ROBOTICS INC                                        8000000     20261015    edgar/data/8000000/0001900000-26-000001.txt
4           HOLLOWAY ALEX                                                 1900000     20261015    edgar/data/1900000/0001900000-26-000001.txt
4/A         GRANITE PEAK BANCORP                                          8000002     20261015    edgar/data/8000002/0001900200-26-000004.txt
4           MERIDIAN FOODS CO                                             8000005     20261015    edgar/data/8000005/0001900500-26-000002.txt
"""

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0008000000</issuerCik><issuerName>Northwind Robotics Inc</issuerName>
    <issuerTradingSymbol>nwrb</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0001900000</rptOwnerCik><rptOwnerName>HOLLOWAY ALEX</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>true</isOfficer>
      <officerTitle>President and Chief Executive Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>0</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-13</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>20000</value></transactionShares>
        <transactionPricePerShare><value>40.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>30000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-14</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>43.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>40000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-10-14</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>43.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>39500</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_index_keeps_unique_form4_only():
    filings = parse_index(INDEX)
    assert [f["accession"] for f in filings] == [
        "0001900000-26-000001", "0001900500-26-000002"]
    assert filings[0]["filed"] == "2026-10-15"


def test_extract_xml_from_submission():
    sub = f"<SEC-DOCUMENT>header\n<DOCUMENT>\n<TYPE>4\n<TEXT>\n<XML>\n{FORM4}\n</XML>\n</TEXT>"
    assert extract_xml(sub).startswith("<ownershipDocument>")
    assert extract_xml("no xml here") is None


def test_parse_form4_fields():
    rows = parse_form4(FORM4, "acc1", "2026-10-15")
    assert len(rows) == 3
    r = rows[0]
    assert r["ticker"] == "NWRB" and r["issuer_cik"] == "8000000"
    assert r["role"] == "CEO" and r["is_director"] and r["is_officer"]
    assert r["value"] == 800000 and r["code"] == "P" and r["direct"]


def test_display_name():
    from pipeline.parse import display_name
    assert display_name("HOLLOWAY ALEX J") == "Alex J Holloway"
    assert display_name("SMITH JOHN JR") == "John Smith Jr"
    assert display_name("BERKSHIRE HATHAWAY INC") == "Berkshire Hathaway Inc"
    assert display_name("Musk Elon") == "Elon Musk"


def test_group_and_score_buy():
    rows = parse_form4(FORM4, "acc1", "2026-10-15")
    trades = group_trades(rows)
    assert len(trades) == 1  # the tax-withholding row (F) is ignored
    t = trades[0]
    assert t["shares"] == 30000 and t["value"] == 1_230_000
    assert t["avg_price"] == 41.0
    assert t["holding_change_pct"] == 300.0  # 10,000 before -> 40,000 after
    assert t["late_days"] == 1

    data = build_site_data(rows, date(2026, 10, 15))
    b = data["buys"][0]
    assert b["strength"] == "High"
    assert "Bought by the CEO" in b["reasons"]


def _trade(**kw):
    base = dict(accession="a", filed="2026-10-14", trade_date="2026-10-13", ticker="ABC",
                company="ABC Corp", issuer_cik="1", insider="P", insider_cik="1",
                role="Director", title="", side="buy", shares=1000, avg_price=10.0,
                value=10000, owned_after=5000, holding_change_pct=25.0,
                new_position=False, plan_10b5_1=False, direct=True, late_days=1)
    base.update(kw)
    return base


def test_clean_ticker():
    from pipeline.signals import clean_ticker
    assert clean_ticker("hei, hei.a") == "HEI"
    assert clean_ticker("N/A") == "" and clean_ticker("NONE") == "" and clean_ticker("") == ""
    assert clean_ticker("BRK.A") == "BRK.A"


def test_bad_price_set_aside():
    from pipeline.signals import flag_bad_prices
    # total typed into the price field: $2.27M deal shown as $10 trillion
    t1 = _trade(accession="x1", ticker="SLBT", shares=4545306, avg_price=2272653.0,
                value=4545306 * 2272653, owned_after=333832129)
    # a real $1,345 stock with no corroboration is kept
    t2 = _trade(accession="x2", ticker="ZZZZ", shares=30000, avg_price=1345.0,
                value=40350000, owned_after=200000)
    # far from what other filings say for the same stock
    t3 = [_trade(accession=f"y{i}", insider_cik=str(i), ticker="QQQX", avg_price=4.0)
          for i in range(3)]
    t4 = _trade(accession="y9", insider_cik="9", ticker="QQQX", avg_price=4000.0, value=4e6)
    bad = flag_bad_prices([t1, t2, *t3, t4])
    assert {b["accession"] for b in bad} == {"x1", "y9"}


def test_twin_filings_merged_but_plans_kept():
    from pipeline.signals import group_trades
    def rows(acc, cik, name):
        return [{**r, "accession": acc, "insider_cik": cik, "insider": name}
                for r in parse_form4(FORM4, acc, "2026-10-15")]
    two = group_trades(rows("a1", "1", "Fund LP") + rows("a2", "2", "Fund GP"))
    assert len(two) == 1 and two[0]["also_reported_by"] == "Fund GP"
    three = group_trades(rows("a1", "1", "A") + rows("a2", "2", "B") + rows("a3", "3", "C"))
    assert len(three) == 3


def test_plan_purchase_not_a_cluster():
    base = parse_form4(FORM4, "a", "2026-10-15")
    rows = []
    for i in range(4):  # same day, same price, four directors
        for r in base[:1]:
            rows.append({**r, "accession": f"p{i}", "insider_cik": str(i),
                         "insider": f"D{i}", "shares": 100 + i, "value": (100 + i) * 40.0})
    data = build_site_data(rows, date(2026, 10, 15))
    assert data["clusters"] == []
    assert all("likely a company plan" in " ".join(b["reasons"]) for b in data["buys"])


def test_cluster_needs_three_insiders():
    base = parse_form4(FORM4, "a", "2026-10-15")
    rows = []
    for i in range(3):  # three people, on different days, at different prices
        for r in base:
            rows.append({**r, "accession": f"a{i}", "insider_cik": str(i), "insider": f"P{i}",
                         "date": f"2026-10-1{i}", "price": r["price"] + i,
                         "value": r["shares"] * (r["price"] + i)})
    data = build_site_data(rows, date(2026, 10, 15))
    assert data["clusters"][0]["insider_count"] == 3
    assert all("Other insiders buying too" in b["reasons"] for b in data["buys"])

    data2 = build_site_data(rows[: len(base) * 2], date(2026, 10, 15))
    assert data2["clusters"] == []
