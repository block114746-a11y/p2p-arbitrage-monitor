import os
import sys
import time
import logging
import requests

# ============================ CONFIG ============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_THRESHOLD_PCT = 1.5      # alert when NET profit % >= this
TRADE_SIZE_USDT = 200.0        # your intended trade size
NETWORK_FEE_USDT = 1.0         # BEP20 USDT withdrawal fee
LOOP_DELAY = 25                # seconds between loops
ALERT_COOLDOWN = 30 * 60       # don't repeat the same alert for 30 min
REQUEST_TIMEOUT = 15
ROWS = 10                      # ads fetched per request

METHODS = ["bKash", "Nagad", "Bank Transfer"]

BINANCE_PAY = {"bKash": "bKash", "Nagad": "Nagad", "Bank Transfer": "BANK"}
BYBIT_PAY = {"bKash": "", "Nagad": "", "Bank Transfer": "14"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Content-Type": "application/json",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p2p")

# ========================== EXCHANGE FETCHERS ==========================
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

def fetch_bybit(method, side):
    pay_id = BYBIT_PAY.get(method)
    if not pay_id:
        raise ValueError(f"No Bybit payment ID set for {method}")
    url = "https://api2.bybit.com/fiat/otc/item/online"
    body = {
        "userId": "", "tokenId": "USDT", "currencyId": "BDT",
        "payment": [pay_id], "side": "1" if side == "buy" else "0",
        "size": str(ROWS), "page": "1", "amount": "",
        "authMaker": False, "canTrade": False,
    }
    r = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    prices = []
    for it in (r.json().get("result") or {}).get("items") or []:
        p = float(it["price"])
        if _fits(it.get("minAmount"), it.get("maxAmount"), p):
            prices.append(p)
    return prices

def fetch_bitget(method, side):
    url = "https://www.bitget.com/v1/p2p/pub/adv/queryAdvList"
    body = {
        "side": 1 if side == "buy" else 2,
        "pageNo": 1, "pageSize": 20,
        "coinCode": "USDT", "fiatCode": "BDT",
        "languageType": 0,
    }
    r = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json().get("data") or {}
    ads = data.get("dataList") or data.get("list") or []
    prices = []
    for ad in ads:
        names = [str(m.get("paymethodName", m.get("name", ""))).lower()
                 for m in (ad.get("paymentMethodList") or ad.get("payMethods") or [])]
        if not any(method.lower().split()[0] in n for n in names):
            continue
        p = float(ad["price"])
        if _fits(ad.get("minAmount"), ad.get("maxAmount"), p):
            prices.append(p)
    return prices

EXCHANGES = {"Binance": fetch_binance, "Bybit": fetch_bybit, "Bitget": fetch_bitget}

# ============================ CORE LOGIC ============================
def collect_quotes():
    quotes = {}
    for ex, fn in EXCHANGES.items():
        for m in METHODS:
            try:
                buy_prices = fn(m, "buy")
                sell_prices = fn(m, "sell")
                q = {}
                if buy_prices:
                    q["ask"] = min(buy_prices)
                if sell_prices:
                    q["bid"] = max(sell_prices)
                if q:
                    quotes.setdefault(ex, {})[m] = q
            except Exception as e:
                log.warning("%s/%s failed: %s", ex, m, e)
            time.sleep(1)
    return quotes

def net_profit_pct(buy_price, sell_price, same_exchange):
    cost_bdt = TRADE_SIZE_USDT * buy_price
    usdt_after_fee = TRADE_SIZE_USDT - (0 if same_exchange else NETWORK_FEE_USDT)
    revenue_bdt = usdt_after_fee * sell_price
    return (revenue_bdt - cost_bdt) / cost_bdt * 100

def best_opportunity(quotes):
    best = None
    for bex, bm in quotes.items():
        for bmeth, bq in bm.items():
            if "ask" not in bq: continue
            for sex, sm in quotes.items():
                for smeth, sq in sm.items():
                    if "bid" not in sq: continue
                    pct = net_profit_pct(bq["ask"], sq["bid"], bex == sex)
                    if best is None or pct > best["net_pct"]:
                        best = {
                            "buy_ex": bex, "buy_method": bmeth, "buy_price": bq["ask"],
                            "sell_ex": sex, "sell_method": smeth, "sell_price": sq["bid"],
                            "gross_pct": (sq["bid"] - bq["ask"]) / bq["ask"] * 100,
                            "net_pct": pct,
                        }
    return best

# ============================ TELEGRAM ============================
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

def format_alert(o):
    profit_bdt = TRADE_SIZE_USDT * o["buy_price"] * o["net_pct"] / 100
    return (
        "🚨 <b>USDT/BDT Arbitrage Alert</b>\n\n"
        f"🟢 <b>BUY</b> on <b>{o['buy_ex']}</b> @ <b>{o['buy_price']:.2f} BDT</b>\n"
        f"   Pay with: {o['buy_method']}\n\n"
        f"🔴 <b>SELL</b> on <b>{o['sell_ex']}</b> @ <b>{o['sell_price']:.2f} BDT</b>\n"
        f"   Receive via: {o['sell_method']}\n\n"
        f"📊 Gross spread: {o['gross_pct']:.2f}%\n"
        f"💰 <b>Net profit: {o['net_pct']:.2f}%</b>\n"
        f"🧮 On {TRADE_SIZE_USDT:.0f} USDT ≈ {profit_bdt:.0f} BDT\n\n"
        "⚠️ Verify live ads and limits before trading."
    )

def format_price_update(quotes):
    msg = "📊 <b>Live P2P Market Update</b>\n\n"
    for ex, methods in quotes.items():
        for m, q in methods.items():
            if "ask" in q:
                msg += f"🟢 {ex} ({m}) Lowest Buy: {q['ask']:.2f} BDT\n"
            if "bid" in q:
                msg += f"🔴 {ex} ({m}) Highest Sell: {q['bid']:.2f} BDT\n"
    return msg

def discover_bybit():
    r = requests.post("https://api2.bybit.com/fiat/otc/configuration/queryAllPaymentList", json={}, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    for p in (r.json().get("result") or {}).get("paymentConfigVo") or []:
        name = str(p.get("paymentName", ""))
        if any(k in name.lower() for k in ("bkash", "nagad", "bank")):
            print(p.get("paymentType"), "->", name)

# ============================ MAIN LOOP ============================
def main():
    log.info("Monitor started. Threshold %.2f%%, size %.0f USDT", ALERT_THRESHOLD_PCT, TRADE_SIZE_USDT)
    last_alert = {}
    last_price_update = 0
    
    while True:
        try:
            quotes = collect_quotes()
            opp = best_opportunity(quotes)
            
            if opp:
                log.info("Best: buy %s @%.2f -> sell %s @%.2f | net %.2f%%",
                         opp["buy_ex"], opp["buy_price"], opp["sell_ex"], opp["sell_price"], opp["net_pct"])
                
                # Alert for Arbitrage Opportunity
                if opp["net_pct"] >= ALERT_THRESHOLD_PCT:
                    key = (opp["buy_ex"], opp["buy_method"], opp["sell_ex"], opp["sell_method"])
                    if time.time() - last_alert.get(key, 0) > ALERT_COOLDOWN:
                        send_telegram(format_alert(opp))
                        last_alert[key] = time.time()
            
            # Alert for Lowest/Highest prices every 1 hour
            if time.time() - last_price_update > 3600: 
                if quotes:
                    send_telegram(format_price_update(quotes))
                    last_price_update = time.time()
                    
        except Exception as e:
            log.error("Loop error: %s", e)
        time.sleep(LOOP_DELAY)

if __name__ == "__main__":
    if "--discover-bybit" in sys.argv:
        discover_bybit()
    elif "--test-telegram" in sys.argv:
        send_telegram("✅ P2P monitor Telegram test OK")
    else:
        main()
