import os
import sys
import time
import logging
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_THRESHOLD_PCT = 1.5      
TRADE_SIZE_USDT = 200.0        
NETWORK_FEE_USDT = 1.0         
REQUEST_TIMEOUT = 15
ROWS = 10                      

METHODS = ["bKash", "Nagad", "Bank Transfer"]
BINANCE_PAY = {"bKash": "bKash", "Nagad": "Nagad", "Bank Transfer": "BANK"}
# আপাতত Bybit এবং Bitget বন্ধ রাখছি, যাতে বিন্যান্সের ডেটা ঠিকমতো আসে
EXCHANGES = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Content-Type": "application/json",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p2p")

def _fits(min_bdt, max_bdt, price):
    try:
        size_bdt = TRADE_SIZE_USDT * price
        return float(min_bdt) <= size_bdt <= float(max_bdt)
    except (TypeError, ValueError):
        return True

def fetch_binance(method, side):
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    body = {
        "fiat": "BDT", "asset": "USDT", "page": 1, "rows": ROWS,
        "tradeType": "BUY" if side == "buy" else "SELL",
        "payTypes": [BINANCE_PAY[method]],
        "publisherType": None, "countries": [],
        "merchantCheck": False, "proMerchantAds": False, "shieldMerchantAds": False,
    }
    r = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    prices = []
    for item in r.json().get("data") or []:
        adv = item["adv"]
        p = float(adv["price"])
        if _fits(adv.get("minSingleTransAmount"), adv.get("maxSingleTransAmount"), p):
            prices.append(p)
    return prices

def collect_quotes():
    quotes = {}
    for m in METHODS:
        try:
            buy_prices = fetch_binance(m, "buy")
            sell_prices = fetch_binance(m, "sell")
            q = {}
            if buy_prices:
                q["ask"] = min(buy_prices)
            if sell_prices:
                q["bid"] = max(sell_prices)
            if q:
                quotes.setdefault("Binance", {})[m] = q
        except Exception as e:
            log.warning("Binance/%s failed: %s", m, e)
        time.sleep(1) 
    return quotes

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials missing")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
    except Exception as e:
        log.error("Telegram send failed: %s", e)

def format_price_update(quotes):
    msg = "📊 <b>Live P2P Market Update (Binance)</b>\n\n"
    for ex, methods in quotes.items():
        for m, q in methods.items():
            if "ask" in q:
                msg += f"🟢 {ex} ({m}) Lowest Buy: {q['ask']:.2f} BDT\n"
            if "bid" in q:
                msg += f"🔴 {ex} ({m}) Highest Sell: {q['bid']:.2f} BDT\n"
    msg += "\n⚠️ Bybit & Bitget are currently paused."
    return msg

def main():
    log.info("Monitor started. Fetching data once...")
    quotes = collect_quotes()
    if quotes:
        msg = format_price_update(quotes)
        send_telegram(msg)
        log.info("Telegram sent successfully.")
    else:
        log.warning("No quotes collected.")

if __name__ == "__main__":
    main()
