import os
import time
import logging
import requests
from datetime import datetime

# ============================ CONFIG ============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_THRESHOLD_PCT = 1.5      
TRADE_SIZE_USDT = 200.0        
NETWORK_FEE_USDT = 1.0         
REQUEST_TIMEOUT = 15
ROWS = 10                      

METHODS = ["bKash", "Nagad", "Bank Transfer"]
BINANCE_PAY = {"bKash": "bKash", "Nagad": "Nagad", "Bank Transfer": "BANK"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Content-Type": "application/json",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p2p")

# ========================== FEE DETECTOR ==========================
# যে শব্দগুলো থাকলে বোঝা যাবে মার্চেন্ট চার্জ নিতে পারে
FEE_KEYWORDS = ["charge", "fee", "extra", "20tk", "20 taka", "15tk", "15 taka", "cash out", "cashout"]
# যে শব্দগুলো থাকলে বোঝা যাবে মার্চেন্ট চার্জ নিচ্ছে না
NO_FEE_KEYWORDS = ["send money", "no fee", "no charge", "free", "0 fee", "without charge", "sendmoney"]

def analyze_fee_status(remarks):
    if not remarks: 
        return "Unknown (Check Chat)"
    
    remarks_lower = remarks.lower()
    
    has_fee_word = any(keyword in remarks_lower for keyword in FEE_KEYWORDS)
    has_no_fee_word = any(keyword in remarks_lower for keyword in NO_FEE_KEYWORDS)
    
    if has_no_fee_word and not has_fee_word:
        return "✅ No Fee (Send Money)"
    elif has_fee_word and not has_no_fee_word:
        return "🚨 Extra Charge Required"
    elif has_fee_word and has_no_fee_word:
        return "⚠️ Ambiguous (Ask in Chat)"
    else:
        return "Unknown (Check Chat)"

# ========================== FETCHERS ==========================
def _fits(min_bdt, max_bdt, price):
    try:
        size_bdt = TRADE_SIZE_USDT * price
        return float(min_bdt) <= size_bdt <= float(max_bdt)
    except (TypeError, ValueError):
        return True

def fetch_binance_ads(method, side):
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    body = {
        "fiat": "BDT", "asset": "USDT", "page": 1, "rows": ROWS,
        "tradeType": "BUY" if side == "buy" else "SELL",
        "payTypes": [BINANCE_PAY[method]],
        "publisherType": None, "countries": [],
        "merchantCheck": False, "proMerchantAds": False, "shieldMerchantAds": False,
    }
    try:
        r = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        ads = []
        for item in r.json().get("data") or []:
            adv = item["adv"]
            p = float(adv["price"])
            if _fits(adv.get("minSingleTransAmount"), adv.get("maxSingleTransAmount"), p):
                remarks = adv.get("remarks", "") or ""
                ads.append({
                    "price": p,
                    "merchant": adv.get("nickName", "Unknown"),
                    "fee_status": analyze_fee_status(remarks)
                })
        return ads
    except Exception as e:
        log.warning(f"Binance/{method} failed: {e}")
        return []

# ============================ CORE LOGIC ============================
def collect_quotes():
    quotes = {}
    for m in METHODS:
        buy_ads = fetch_binance_ads(m, "buy")
        sell_ads = fetch_binance_ads(m, "sell")
        
        if buy_ads or sell_ads:
            quotes[m] = {
                "buy": sorted(buy_ads, key=lambda x: x["price"])[:3], # সেরা ৩টি কম দাম
                "sell": sorted(sell_ads, key=lambda x: x["price"], reverse=True)[:3] # সেরা ৩টি বেশি দাম
            }
        time.sleep(1)
    return quotes

def find_best_opportunity(quotes):
    best = None
    for buy_method, data in quotes.items():
        if not data.get("buy"): continue
        
        for sell_method, sell_data in quotes.items():
            if not sell_data.get("sell"): continue
            
            # সেরা দামগুলো নেওয়া
            best_buy = data["buy"][0]
            best_sell = sell_data["sell"][0]
            
            buy_price = best_buy["price"]
            sell_price = best_sell["price"]
            
            if sell_price <= buy_price: continue
            
            net_pct = ((sell_price - buy_price) / buy_price) * 100
            
            if best is None or net_pct > best["net_pct"]:
                best = {
                    "buy_method": buy_method, "buy_data": best_buy,
                    "sell_method": sell_method, "sell_data": best_sell,
                    "net_pct": net_pct, 
                    "profit_bdt": (sell_price - buy_price) * TRADE_SIZE_USDT
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
    now = datetime.now().strftime("%I:%M %p, %d %b %Y")
    
    msg = "📊 <b>PRO P2P ARBITRAGE MONITOR</b>\n"
    msg += f"🕒 <i>{now}</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # সেরা লাভের সুযোগ
    if best and best["net_pct"] > 0:
        b = best["buy_data"]
        s = best["sell_data"]
        
        msg += "💎 <b>BEST ARBITRAGE STRATEGY</b> 💎\n\n"
        msg += f"🛒 <b>BUY</b> from <b>{best['buy_method']}</b>\n"
        msg += f"   Rate: <b>{b['price']:.2f} BDT</b>\n"
        msg += f"   Merchant: {b['merchant']}\n"
        msg += f"   Fee Status: {b['fee_status']}\n\n"
            
        msg += f"💰 <b>SELL</b> to <b>{best['sell_method']}</b>\n"
        msg += f"   Rate: <b>{s['price']:.2f} BDT</b>\n"
        msg += f"   Merchant: {s['merchant']}\n"
        msg += f"   Fee Status: {s['fee_status']}\n\n"

        msg += f"📈 <b>Net Profit:</b> <u>{best['net_pct']:.2f}%</u>\n"
        msg += f"💵 <b>Est. Profit:</b> ~{best['profit_bdt']:.0f} BDT <i>(on {TRADE_SIZE_USDT:.0f} USDT)</i>\n\n"
        
        msg += "💡 <b>Instructions:</b>\n"
        msg += "1. Always use <b>'Send Money'</b>, never 'Cash Out'.\n"
        msg += "2. If the merchant asks for extra charges, cancel the order immediately.\n\n"
        
        if best["net_pct"] >= ALERT_THRESHOLD_PCT:
            msg += "🔥 <b>HIGH PROFIT ALERT! EXECUTE NOW!</b> 🔥"
    else:
        msg += "❌ <i>No profitable arbitrage opportunities found right now.</i>\n"

    msg += "\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += "🔍 <b>TOP MERCHANTS & FEE ANALYSIS</b>\n\n"

    # পেমেন্ট মেথড অনুযায়ী টপ ৩ মার্চেন্ট দেখানো
    for method, data in quotes.items():
        msg += f"💳 <b>{method}</b>\n"
        
        if data.get("buy"):
            msg += "   🟢 <b>Top Buy Ads (Lowest):</b>\n"
            for ad in data["buy"]:
                msg += f"      • {ad['price']:.2f} BDT | {ad['fee_status']}\n"
        
        if data.get("sell"):
            msg += "   🔴 <b>Top Sell Ads (Highest):</b>\n"
            for ad in data["sell"]:
                msg += f"      • {ad['price']:.2f} BDT | {ad['fee_status']}\n"
        msg += "\n"

    return msg

def main():
    log.info("Fetching data...")
    quotes = collect_quotes()
    best = find_best_opportunity(quotes)
    msg = format_market_update(quotes, best)
    send_telegram(msg)
    log.info("Update sent.")

if __name__ == "__main__":
    main()
