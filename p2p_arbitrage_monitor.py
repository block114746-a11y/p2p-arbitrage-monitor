import os
import time
import logging
import requests
from datetime import datetime

# ============================ CONFIG ============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_THRESHOLD_PCT = 1.5      # ১.৫% বা তার বেশি লাভ হলে রেড অ্যালার্ট যাবে
TRADE_SIZE_USDT = 200.0        # আপনি কত ডলারের ট্রেড করবেন
NETWORK_FEE_USDT = 1.0         # নেটওয়ার্ক ফি
REQUEST_TIMEOUT = 15
ROWS = 5                       # প্রতি রিকোয়েস্টে কতটি অ্যাড দেখবে

METHODS = ["bKash", "Nagad", "Bank Transfer"]

BINANCE_PAY = {"bKash": "bKash", "Nagad": "Nagad", "Bank Transfer": "BANK"}
BYBIT_PAY = {"bKash": "14", "Nagad": "15", "Bank Transfer": "16"} 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Content-Type": "application/json",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p2p")

# ========================== FETCHERS ==========================
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
    if not pay_id: return []
    url = "https://api2.bybit.com/fiat/otc/item/online"
    body = {
        "userId": "", "tokenId": "USDT", "currencyId": "BDT",
        "payment": [pay_id], "side": "1" if side == "buy" else "0",
        "size": str(ROWS), "page": "1", "amount": "",
        "authMaker": False, "canTrade": False,
    }
    try:
        r = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        prices = []
        for it in (r.json().get("result") or {}).get("items") or []:
            p = float(it["price"])
            if _fits(it.get("minAmount"), it.get("maxAmount"), p):
                prices.append(p)
        return prices
    except Exception:
        return []

def fetch_bitget(method, side):
    url = "https://www.bitget.com/v1/p2p/pub/adv/queryAdvList"
    body = {
        "side": 1 if side == "buy" else 2,
        "pageNo": 1, "pageSize": 20,
        "coinCode": "USDT", "fiatCode": "BDT", "languageType": 0,
    }
    try:
        r = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json().get("data") or {}
        ads = data.get("dataList") or data.get("list") or []
        prices = []
        for ad in ads:
            names = [str(m.get("paymethodName", m.get("name", ""))).lower() for m in (ad.get("paymentMethodList") or ad.get("payMethods") or [])]
            if not any(method.lower().split()[0] in n for n in names): continue
            p = float(ad["price"])
            if _fits(ad.get("minAmount"), ad.get("maxAmount"), p):
                prices.append(p)
        return prices
    except Exception:
        return []

EXCHANGES = {"Binance": fetch_binance, "Bybit": fetch_bybit, "Bitget": fetch_bitget}

# ============================ CORE LOGIC ============================
def collect_quotes():
    quotes = {}
    for ex_name, fetch_func in EXCHANGES.items():
        for m in METHODS:
            try:
                buy_prices = fetch_func(m, "buy")
                sell_prices = fetch_func(m, "sell")
                q = {}
                if buy_prices: q["ask"] = min(buy_prices)
                if sell_prices: q["bid"] = max(sell_prices)
                if q: quotes.setdefault(ex_name, {})[m] = q
            except Exception as e:
                pass
            time.sleep(0.5)
    return quotes

def find_best_opportunity(quotes):
    best = None
    for buy_ex, methods in quotes.items():
        for buy_method, q in methods.items():
            if "ask" not in q: continue
            for sell_ex, sell_methods in quotes.items():
                for sell_method, sq in sell_methods.items():
                    if "bid" not in sq: continue
                    
                    buy_price = q["ask"]
                    sell_price = sq["bid"]
                    
                    # Skip if buying and selling on the same exchange with no profit
                    if buy_ex == sell_ex and buy_method == sell_method: continue
                    if sell_price <= buy_price: continue

                    # Calculate profit
                    cost = TRADE_SIZE_USDT * buy_price
                    fee = 0 if buy_ex == sell_ex else NETWORK_FEE_USDT
                    revenue = (TRADE_SIZE_USDT - fee) * sell_price
                    net_pct = ((revenue - cost) / cost) * 100

                    if best is None or net_pct > best["net_pct"]:
                        best = {
                            "buy_ex": buy_ex, "buy_method": buy_method, "buy_price": buy_price,
                            "sell_ex": sell_ex, "sell_method": sell_method, "sell_price": sell_price,
                            "net_pct": net_pct, "profit_bdt": revenue - cost
                        }
    return best

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

def format_market_update(quotes, best):
    # Get current time
    now = datetime.now().strftime("%I:%M %p, %d %b %Y")
    
    msg = "📊 <b>P2P ARBITRAGE MONITOR</b>\n"
    msg += f"🕒 <i>{now}</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Show lowest/highest for each exchange
    for ex, methods in quotes.items():
        msg += f"🏦 <b>{ex}</b>\n"
        for m, q in methods.items():
            if "ask" in q: msg += f"   🟢 Buy: <b>{q['ask']:.2f}</b> BDT <i>({m})</i>\n"
            if "bid" in q: msg += f"   🔴 Sell: <b>{q['bid']:.2f}</b> BDT <i>({m})</i>\n"
        msg += "\n"
        
    if not quotes.get("Bitget") and not quotes.get("Bybit"):
        msg += "⚠️ <i>Bitget & Bybit data blocked by their server. (Binance is active)</i>\n\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # Suggest best arbitrage
    if best and best["net_pct"] > 0:
        msg += "💎 <b>BEST OPPORTUNITY</b> 💎\n\n"
        msg += f"🛒 <b>BUY</b> from <b>{best['buy_ex']}</b>\n"
        msg += f"   Rate: <b>{best['buy_price']:.2f} BDT</b> (via {best['buy_method']})\n\n"
        msg += f"💰 <b>SELL</b> to <b>{best['sell_ex']}</b>\n"
        msg += f"   Rate: <b>{best['sell_price']:.2f} BDT</b> (via {best['sell_method']})\n\n"
        msg += f"📈 <b>Net Profit:</b> <u>{best['net_pct']:.2f}%</u>\n"
        msg += f"💵 <b>Est. Profit:</b> ~{best['profit_bdt']:.0f} BDT <i>(on {TRADE_SIZE_USDT:.0f} USDT)</i>\n\n"
        
        if best["net_pct"] >= ALERT_THRESHOLD_PCT:
            msg += "🔥 <b>HIGH PROFIT ALERT! EXECUTE NOW!</b> 🔥"
        else:
            msg += "⚠️ <i>Below 1.5% threshold. Keep waiting.</i>"
    else:
        msg += "❌ <i>No profitable arbitrage opportunities found right now.</i>"
        
    return msg

def main():
    log.info("Fetching data from all exchanges...")
    quotes = collect_quotes()
    best = find_best_opportunity(quotes)
    msg = format_market_update(quotes, best)
    send_telegram(msg)
    log.info("Update sent to Telegram.")

if __name__ == "__main__":
    main()
