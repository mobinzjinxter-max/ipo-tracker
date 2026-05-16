#!/usr/bin/env python3
"""
IPO Tracker - Monitors SEC EDGAR for IPO filings from target companies
and sends Telegram notifications.
"""

import requests
import json
import os
import sys
from datetime import datetime, timedelta

# ── Configuration ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
SCRIPT_DIR         = os.path.dirname(os.path.abspath(__file__))
SEEN_FILINGS_FILE  = os.path.join(SCRIPT_DIR, "seen_filings.json")
LOG_FILE           = os.path.join(SCRIPT_DIR, "ipo_tracker.log")

COMPANIES = [
    "Anthropic",
    "Databricks",
    "SpaceX",
    "Cerebras Systems",
    "Lambda Labs",
    "Trailblazer",
    "OpenAI",
    "Stripe",
    "Chime",
    "Klarna",
    "Anduril",
    "Shield AI",
    "Shein",
    "Fanatics",
    "Starlink",
    "Space Exploration Technologies",
    "Averin Capital Acquisition",
    "Discord",
]

# 424B4 = final prospectus filed the day a stock is priced and begins trading
IPO_FORMS = "424B4,424B3"

HEADERS = {
    "User-Agent": "IPO Tracker Bot ipo-tracker@gmail.com",
    "Accept":     "application/json",
}

# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ── Telegram ───────────────────────────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     message,
        "parse_mode":               "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        result = r.json()
        if not result.get("ok"):
            log(f"Telegram error: {result}")
        return result.get("ok", False)
    except Exception as e:
        log(f"Telegram exception: {e}")
        return False

# ── SEC EDGAR ──────────────────────────────────────────────────────────────────
def search_edgar(company: str) -> list:
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q":         f'"{company}"',
        "forms":     IPO_FORMS,
        "dateRange": "custom",
        "startdt":   (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        "enddt":     datetime.now().strftime("%Y-%m-%d"),
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json().get("hits", {}).get("hits", [])
    except Exception as e:
        log(f"EDGAR error for '{company}': {e}")
        return []

def is_filing_by_company(hit: dict, company: str) -> bool:
    """Return True only if this filing was made BY the target company, not just a mention."""
    display_names = hit.get("_source", {}).get("display_names", [])
    if not display_names:
        return False
    # display_names[0] looks like: "Cerebras Systems Inc.  (CBRS)  (CIK 0002021728)"
    # Extract the company name portion (before the first parenthesis)
    entity_name = display_names[0].split("(")[0].strip().lower()
    return company.lower() in entity_name

def build_filing_url(src: dict) -> str:
    ciks = src.get("ciks", [])
    adsh = src.get("adsh", "")
    if ciks and adsh:
        cik_int = int(ciks[0])
        acc_no  = adsh.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no}/"
    return "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=S-1"

# ── State persistence ──────────────────────────────────────────────────────────
def load_seen() -> dict:
    if os.path.exists(SEEN_FILINGS_FILE):
        with open(SEEN_FILINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_seen(seen: dict):
    with open(SEEN_FILINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)

# ── Core check ─────────────────────────────────────────────────────────────────
def check_filings():
    log("Starting SEC EDGAR scan...")
    seen  = load_seen()
    found = 0

    for company in COMPANIES:
        hits = search_edgar(company)
        for hit in hits:
            src       = hit.get("_source", {})
            # Use accession number as unique key — multiple docs share one submission
            filing_id = src.get("adsh") or hit.get("_id", "")
            if not filing_id or filing_id in seen:
                continue

            # Only alert when the filing entity IS the tracked company
            if not is_filing_by_company(hit, company):
                continue

            seen[filing_id] = datetime.now().isoformat()
            form_type   = src.get("form", src.get("file_type", "?"))
            filed_date  = src.get("file_date", "?")
            root_form   = src.get("root_forms", ["?"])[0]
            display     = src.get("display_names", [company])[0]
            entity      = display.split("(")[0].strip()
            ticker_part = ""
            # Extract ticker if present: "Company Name  (TICK)  (CIK ...)"
            parts = display.split("(")
            if len(parts) >= 2 and "CIK" not in parts[1]:
                ticker_part = f" | Ticker: {parts[1].rstrip(') ')}"

            filing_url  = build_filing_url(src)

            msg = (
                f"\U0001F7E2 <b>IPO NOW OPEN FOR TRADING</b>\n\n"
                f"<b>Company:</b> {entity}{ticker_part}\n"
                f"<b>Form:</b> {form_type}\n"
                f"<b>Date:</b> {filed_date}\n"
                f'<a href="{filing_url}">View Final Prospectus (SEC) →</a>'
            )
            if send_telegram(msg):
                log(f"Notified: {entity} | {form_type} | {filed_date}")
            found += 1

    save_seen(seen)
    log(f"Scan complete — {found} new filing(s).")

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = send_telegram(
            "✅ <b>IPO Tracker is active!</b>\n\n"
            "You'll receive an alert only when a tracked company files "
            "their final 424B4 prospectus — meaning the stock is <b>priced "
            "and open for trading that day</b>.\n\n"
            "<b>Tracked companies:</b>\n"
            + "\n".join(f"• {c}" for c in COMPANIES)
        )
        log("Test message sent." if ok else "Test message FAILED.")
    else:
        check_filings()
