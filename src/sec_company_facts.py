#!/usr/bin/env python3

import argparse
import json
import time
import urllib.request
from datetime import date
from pathlib import Path

COMPANIES = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
}
REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
)
OPERATING_INCOME_TAG = "OperatingIncomeLoss"
API_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare latest-fiscal-year revenue, growth, and operating margin."
    )
    parser.add_argument("--email", required=True, help="Email for the SEC User-Agent")
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data"), help="Raw JSON directory"
    )
    return parser.parse_args()


def download_company_facts(ticker, cik, email, data_dir):
    path = data_dir / f"{ticker}_companyfacts.json"
    if path.exists():
        return path

    request = urllib.request.Request(
        API_URL.format(cik=cik),
        headers={
            "User-Agent": f"aicgbf-company-facts/1.0 ({email})",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    with path.open("xb") as output:
        output.write(payload)
    return path


def annual_facts(company_facts, tag):
    fact = company_facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if not fact:
        return []

    result = []
    for item in fact.get("units", {}).get("USD", []):
        if item.get("form") not in {"10-K", "10-K/A"} or item.get("fp") != "FY":
            continue
        if not item.get("start") or not item.get("end"):
            continue
        days = (date.fromisoformat(item["end"]) - date.fromisoformat(item["start"])).days
        if 330 <= days <= 400:
            result.append(item)
    return result


def latest_for_period(facts, end=None):
    candidates = [fact for fact in facts if end is None or fact["end"] == end]
    if not candidates:
        return None
    return max(candidates, key=lambda fact: (fact["filed"], fact.get("accn", "")))


def select_revenue(company_facts):
    candidates = []
    for priority, tag in enumerate(REVENUE_TAGS):
        facts = annual_facts(company_facts, tag)
        if facts:
            latest_end = max(fact["end"] for fact in facts)
            candidates.append((latest_end, -priority, tag, facts))
    if not candidates:
        return None, None, None

    latest_end, _, tag, facts = max(candidates, key=lambda candidate: candidate[:2])
    current = latest_for_period(facts, latest_end)
    earlier_ends = [fact["end"] for fact in facts if fact["end"] < current["end"]]
    previous = latest_for_period(facts, max(earlier_ends)) if earlier_ends else None
    return tag, current, previous


def select_operating_income(company_facts, period_end):
    facts = annual_facts(company_facts, OPERATING_INCOME_TAG)
    return latest_for_period(facts, period_end)


def billions(value):
    return value / 1_000_000_000 if value is not None else None


def money(value):
    return f"${billions(value):,.1f}B" if value is not None else "n/a"


def percentage(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return "n/a"
    return f"{numerator / denominator * 100:.1f}%"


def fact_line(label, tag, fact):
    if fact is None:
        return f"  {label}: n/a (tag={tag or 'n/a'})"
    return (
        f"  {label}: {money(fact['val'])}; XBRL tag={tag}; "
        f"period={fact['start']} to {fact['end']}; filed={fact['filed']}; "
        f"accession={fact.get('accn', 'n/a')}"
    )


def main():
    args = parse_args()
    if "@" not in args.email:
        raise SystemExit("A valid email is required for the SEC User-Agent.")
    args.data_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    provenance = []
    for position, (ticker, cik) in enumerate(COMPANIES.items()):
        path = download_company_facts(ticker, cik, args.email, args.data_dir)
        with path.open(encoding="utf-8") as source:
            company_facts = json.load(source)

        revenue_tag, current_revenue, previous_revenue = select_revenue(company_facts)
        period_end = current_revenue["end"] if current_revenue else None
        operating_income = (
            select_operating_income(company_facts, period_end) if period_end else None
        )
        current_value = current_revenue["val"] if current_revenue else None
        previous_value = previous_revenue["val"] if previous_revenue else None
        operating_value = operating_income["val"] if operating_income else None
        growth = (
            percentage(current_value - previous_value, previous_value)
            if current_value is not None and previous_value is not None
            else "n/a"
        )

        rows.append(
            (
                ticker,
                period_end or "n/a",
                money(current_value),
                growth,
                money(operating_value),
                percentage(operating_value, current_value),
            )
        )
        provenance.extend(
            (
                f"{ticker} (raw JSON: {path})",
                fact_line("Current revenue", revenue_tag, current_revenue),
                fact_line("Prior-year revenue", revenue_tag, previous_revenue),
                fact_line("Operating income", OPERATING_INCOME_TAG, operating_income),
            )
        )
        if position < len(COMPANIES) - 1:
            time.sleep(0.2)

    headers = ("Ticker", "FY end", "Revenue", "YoY growth", "Operating income", "Operating margin")
    widths = [max(len(str(row[index])) for row in [headers] + rows) for index in range(6)]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))

    print("\nValue provenance")
    print("\n".join(provenance))
    print(
        "\nGrowth and operating margin are calculated from the values above; "
        "they are not directly reported XBRL values."
    )
    print(
        "Fiscal-year mismatch: AAPL, MSFT, and NVDA have non-calendar fiscal-year ends; "
        "GOOGL and AMZN use December 31. These are each firm's latest fiscal year, "
        "not a common calendar period."
    )


if __name__ == "__main__":
    main()
