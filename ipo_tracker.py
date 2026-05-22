#!/usr/bin/env python3
"""
IPO Tracker + Buy Signal Monitor
- Watches SEC EDGAR for 424B4 filings (IPO open for trading)
- Watches 8 stocks for huge buy signals via volume, price, RSI, Golden Cross
- Writes docs/alerts.json for the iPhone PWA dashboard
"""

import requests
import json
import os
import sys
from datetime import datetime, timedelta

# ── Configuration ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
VAPID_PRIVATE_KEY   = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY    = os.environ.get("VAPID_PUBLIC_KEY", "")
PUSH_SUBSCRIPTION   = os.environ.get("PUSH_SUBSCRIPTION", "")  # JSON string
SCRIPT_DIR         = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE          = os.path.join(SCRIPT_DIR, "seen_filings.json")
ALERTS_FILE        = os.path.join(SCRIPT_DIR, "docs", "alerts.json")
CUSTOM_FILE        = os.path.join(SCRIPT_DIR, "docs", "custom_watches.json")
LOG_FILE           = os.path.join(SCRIPT_DIR, "ipo_tracker.log")

# ── IPO watch list ─────────────────────────────────────────────────────────────
COMPANIES = [
    "Anthropic", "Databricks", "SpaceX", "Cerebras Systems",
    "Lambda Labs", "Trailblazer", "OpenAI", "Stripe", "Chime",
    "Klarna", "Anduril", "Shield AI", "Shein", "Fanatics",
    "Starlink", "Space Exploration Technologies",
    "Averin Capital Acquisition", "Discord",
]
IPO_FORMS = "424B4,424B3"

# ── Stock buy-signal watch list ────────────────────────────────────────────────
WATCH_STOCKS = {
    "SanDisk":          "SNDK",
    "Astera Labs":      "ALAB",
    "Bloom Energy":     "BE",
    "Credo Technology": "CRDO",
    "X-energy":         "XE",
    "NuScale Power":    "SMR",
    "Oklo":             "OKLO",
    "Rocket Lab":       "RKLB",
}

VOLUME_SURGE_X  = 2.5   # volume must be 2.5x the 20-day average
PRICE_SURGE_PCT = 4.0   # price up >= 4% on the day
RSI_BREAKOUT    = 68    # RSI >= 68 with volume surge = momentum breakout

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
        "disable_web_page_preview": True,
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

# ── State persistence ──────────────────────────────────────────────────────────
def load_seen() -> dict:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_seen(seen: dict):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)

# ── Custom watchlist (added via the PWA) ──────────────────────────────────────
def load_custom_watches() -> tuple:
    """Return (extra_companies: list, extra_stocks: dict) from custom_watches.json."""
    if not os.path.exists(CUSTOM_FILE):
        return [], {}
    try:
        with open(CUSTOM_FILE, encoding="utf-8") as f:
            data = json.load(f)
        companies = [c for c in data.get("ipo_companies", []) if isinstance(c, str) and c.strip()]
        stocks    = {
            s["name"]: s["ticker"]
            for s in data.get("stocks", [])
            if isinstance(s, dict) and s.get("name") and s.get("ticker")
        }
        return companies, stocks
    except Exception as e:
        log(f"Custom watches load error: {e}")
        return [], {}

# ── Alerts dashboard data ──────────────────────────────────────────────────────
def load_alerts() -> dict:
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"ipo_alerts": [], "buy_signals": [], "last_updated": ""}

def save_alerts(data: dict):
    os.makedirs(os.path.dirname(ALERTS_FILE), exist_ok=True)
    data["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    data["watched_companies"] = COMPANIES
    data["watched_stocks"] = WATCH_STOCKS
    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ── Web Push ───────────────────────────────────────────────────────────────────
def send_push(title: str, body: str, alert_type: str = "info") -> bool:
    if not PUSH_SUBSCRIPTION or not VAPID_PRIVATE_KEY:
        return False
    try:
        from pywebpush import webpush, WebPushException
        sub = json.loads(PUSH_SUBSCRIPTION)
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body, "type": alert_type}),
            vapid_private_key=VAPID_PRIVATE_KEY,   # raw base64url EC key
            vapid_claims={"sub": "mailto:ipo-tracker@gmail.com"},
        )
        log(f"Push sent: {title}")
        return True
    except Exception as e:
        log(f"Push error: {e}")
        if hasattr(e, "response") and e.response:
            log(f"Push response: {e.response.text[:200]}")
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
    display_names = hit.get("_source", {}).get("display_names", [])
    if not display_names:
        return False
    entity_name = display_names[0].split("(")[0].strip().lower()
    return company.lower() in entity_name

def build_filing_url(src: dict) -> str:
    ciks = src.get("ciks", [])
    adsh = src.get("adsh", "")
    if ciks and adsh:
        return f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{adsh.replace('-','')}/"
    return "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=424B4"

# ── IPO check ──────────────────────────────────────────────────────────────────
def check_filings(seen: dict, alerts: dict, companies: list = None) -> int:
    companies = companies if companies is not None else COMPANIES
    log(f"Scanning SEC EDGAR for IPO filings ({len(companies)} companies)...")
    found = 0
    for company in companies:
        for hit in search_edgar(company):
            src       = hit.get("_source", {})
            filing_id = src.get("adsh") or hit.get("_id", "")
            if not filing_id or filing_id in seen:
                continue
            if not is_filing_by_company(hit, company):
                continue

            seen[filing_id] = datetime.now().isoformat()
            form_type  = src.get("form", "?")
            filed_date = src.get("file_date", "?")
            display    = src.get("display_names", [company])[0]
            entity     = display.split("(")[0].strip()
            parts      = display.split("(")
            ticker     = parts[1].rstrip(") ") if len(parts) >= 2 and "CIK" not in parts[1] else ""
            url        = build_filing_url(src)

            # Telegram
            ticker_str = f" ({ticker})" if ticker else ""
            msg = (
                f"\U0001F7E2 <b>IPO NOW OPEN FOR TRADING</b>\n\n"
                f"<b>Company:</b> {entity}{ticker_str}\n"
                f"<b>Form:</b> {form_type}\n"
                f"<b>Date:</b> {filed_date}\n"
                f'<a href="{url}">View Final Prospectus →</a>'
            )
            if send_telegram(msg):
                log(f"IPO alert: {entity} | {form_type} | {filed_date}")
            send_push(
                title=f"🟢 IPO NOW TRADING — {entity}{ticker_str}",
                body=f"Filed {form_type} on {filed_date}. Tap to view SEC prospectus.",
                alert_type="ipo"
            )

            # Dashboard
            alerts["ipo_alerts"].insert(0, {
                "company":   entity,
                "ticker":    ticker,
                "form":      form_type,
                "date":      filed_date,
                "url":       url,
                "timestamp": datetime.now().isoformat(),
            })
            found += 1

    # Keep only last 50 IPO alerts
    alerts["ipo_alerts"] = alerts["ipo_alerts"][:50]
    return found

# ── Buy signal check ───────────────────────────────────────────────────────────
def calc_rsi(closes: list, period: int = 14) -> float:
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))

def check_buy_signals(seen: dict, alerts: dict, stocks: dict = None) -> int:
    stocks = stocks if stocks is not None else WATCH_STOCKS
    try:
        import yfinance as yf
    except ImportError:
        log("yfinance not installed — skipping buy signals")
        return 0

    log(f"Scanning buy signals ({len(stocks)} stocks)...")
    found     = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Keep existing buy signal history for the dashboard
    existing_signals = {s["ticker"] + s["date"]: s for s in alerts.get("buy_signals", [])}

    for name, ticker in stocks.items():
        try:
            hist = yf.Ticker(ticker).history(period="220d")
            if len(hist) < 22:
                log(f"Not enough data for {ticker} — may not be listed yet")
                continue

            closes  = hist["Close"].tolist()
            volumes = hist["Volume"].tolist()

            current_price = closes[-1]
            prev_close    = closes[-2]
            current_vol   = volumes[-1]
            avg_vol_20    = sum(volumes[-21:-1]) / 20

            price_chg_pct = ((current_price - prev_close) / prev_close) * 100
            vol_ratio     = current_vol / avg_vol_20 if avg_vol_20 > 0 else 0
            rsi           = calc_rsi(closes[-30:])

            golden_cross = False
            if len(closes) >= 201:
                ma50_prev  = sum(closes[-51:-1]) / 50
                ma200_prev = sum(closes[-201:-1]) / 200
                ma50_now   = sum(closes[-50:]) / 50
                ma200_now  = sum(closes[-200:]) / 200
                golden_cross = ma50_prev <= ma200_prev and ma50_now > ma200_now

            signals = []
            if vol_ratio >= VOLUME_SURGE_X and price_chg_pct >= PRICE_SURGE_PCT:
                signals.append(f"Volume surge {vol_ratio:.1f}x + Price +{price_chg_pct:.1f}%")
            if rsi >= RSI_BREAKOUT and vol_ratio >= 1.5:
                signals.append(f"RSI momentum breakout (RSI {rsi:.0f}, vol {vol_ratio:.1f}x)")
            if golden_cross:
                signals.append("Golden Cross — 50MA crossed above 200MA")

            if not signals:
                continue

            signal_key = f"signal_{ticker}_{today_str}"
            if signal_key in seen:
                continue

            seen[signal_key] = datetime.now().isoformat()

            # Telegram
            emoji_signals = []
            for s in signals:
                if "Volume" in s:   emoji_signals.append("🔥 " + s)
                elif "RSI" in s:    emoji_signals.append("⚡ " + s)
                elif "Golden" in s: emoji_signals.append("⭐ " + s)
                else:               emoji_signals.append("📊 " + s)

            msg = (
                f"📈 <b>BUY SIGNAL — {name} ({ticker})</b>\n\n"
                f"<b>Price:</b> ${current_price:.2f}  ({price_chg_pct:+.1f}% today)\n"
                f"<b>Volume:</b> {vol_ratio:.1f}x 20-day avg\n"
                f"<b>RSI (14):</b> {rsi:.0f}\n\n"
                f"<b>Signals:</b>\n" + "\n".join(emoji_signals)
            )
            if send_telegram(msg):
                log(f"Buy signal: {name} ({ticker}) | {' | '.join(signals)}")
            send_push(
                title=f"📈 BUY SIGNAL — {name} ({ticker})",
                body=f"${current_price:.2f}  {price_chg_pct:+.1f}%  ·  {signals[0]}",
                alert_type="signal"
            )

            # Dashboard
            alerts["buy_signals"].insert(0, {
                "name":          name,
                "ticker":        ticker,
                "price":         round(current_price, 2),
                "price_chg_pct": round(price_chg_pct, 2),
                "vol_ratio":     round(vol_ratio, 2),
                "rsi":           round(rsi, 1),
                "signals":       signals,
                "date":          today_str,
                "timestamp":     datetime.now().isoformat(),
            })
            found += 1

        except Exception as e:
            log(f"Signal error for {name} ({ticker}): {e}")

    # Keep last 100 buy signal entries
    alerts["buy_signals"] = alerts["buy_signals"][:100]
    return found

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = send_telegram(
            "✅ <b>Tracker is active!</b>\n\n"
            "<b>IPO Watch:</b> Alerts when 424B4 prospectus is filed "
            "(stock open for trading that day).\n\n"
            "<b>Buy Signal Watch:</b>\n"
            + "\n".join(f"• {n} ({t})" for n, t in WATCH_STOCKS.items())
            + "\n\nSignals: Volume ≥2.5x, Price +4%, RSI breakout, Golden Cross."
        )
        log("Test message sent." if ok else "Test message FAILED.")
    else:
        seen   = load_seen()
        alerts = load_alerts()

        # Merge hardcoded lists with custom additions from the PWA
        custom_companies, custom_stocks = load_custom_watches()
        all_companies = COMPANIES + [c for c in custom_companies if c not in COMPANIES]
        all_stocks    = {**WATCH_STOCKS, **custom_stocks}
        if custom_companies:
            log(f"Custom IPO companies: {custom_companies}")
        if custom_stocks:
            log(f"Custom stocks: {list(custom_stocks.keys())}")

        ipo_count    = check_filings(seen, alerts, all_companies)
        signal_count = check_buy_signals(seen, alerts, all_stocks)

        # Write merged lists to alerts.json so the PWA can display them
        alerts["watched_companies"] = all_companies
        alerts["watched_stocks"]    = all_stocks

        save_seen(seen)
        save_alerts(alerts)
        log(f"Done — {ipo_count} IPO alert(s), {signal_count} buy signal(s).")
