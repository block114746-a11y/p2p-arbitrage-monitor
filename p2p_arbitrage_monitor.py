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

# ========================== SMART FEE DETECTOR ==========================
FEE_KEYWORDS = ["charge", "fee", "extra", "20tk", "20 taka", "15tk", "15 taka", "cash out", "cashout", "20৳", "15৳", "kata"]
NO_FEE_KEYWORDS = ["send money", "no fee", "no charge", "free", "0 fee", "without charge", "sendmoney", "send money only"]

def analyze_fee_status(remarks):
    if not remarks: 
        return "✅ No Fee Mentioned"
    remarks_lower = remarks.lower()
    has_fee = any(keyword in remarks_lower for keyword in FEE_KEYWORDS)
    has_no_fee = any(keyword in remarks_lower for keyword in NO_FEE_KEYWORDS)
    
    if has_no_fee and not has_fee:
        return "✅ No Fee (Send Money)"
    elif has_fee:
        return "🚨 Hidden Charge!"
    else:
        return "✅ No Fee Mentioned"

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
                advertiser = item.get("advertiser", {})
                merchant_name = advertiser.get("nickName", "Unknown")
                remarks = adv.get("remarks", "") or ""
                
                ads.append({
                    "price": p,
                    "merchant": merchant_name,
                    "fee_status": analyze_fee_status(remarks)
                })
        return ads
    except Exception as e:
        log.warning(f"Binance/{method} failed: {e}")
        return []

# ============================ CORE LOGIC ============================
def collect_market_data():
    market_data = {}
    for m in METHODS:
        buy_ads = fetch_binance_ads(m, "buy")
        sell_ads = fetch_binance_ads(m, "sell")
        if buy_ads or sell_ads:
            market_data[m] = {
                "buy": sorted(buy_ads, key=lambda x: x["price"])[:2], 
                "sell": sorted(sell_ads, key=lambda x: x["price"], reverse=True)[:2] 
            }
        time.sleep(0.5)
    return market_data

def find_best_opportunity(market_data):
    best = None
    for buy_method, data in market_data.items():
        if not data.get("buy"): continue
        for sell_method, sell_data in market_data.items():
            if not sell_data.get("sell"): continue
            
            best_buy = data["buy"][0]
            best_sell = sell_data["sell"][0]
            
            if best_sell["price"] <= best_buy["price"]: continue
            
            net_pct = ((best_sell["price"] - best_buy["price"]) / best_buy["price"]) * 100
            
            if best is None or net_pct > best["net_pct"]:
                best = {
                    "buy_method": buy_method, "buy_data": best_buy,
                    "sell_method": sell_method, "sell_data": best_sell,
                    "net_pct": net_pct, 
                    "profit_bdt": (best_sell["price"] - best_buy["price"]) * TRADE_SIZE_USDT
                }
    return best

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

def format_full_analysis(market_data, best):
    now = datetime.now().strftime("%I:%M %p, %d %b %Y")
    
    msg = "📊 <b>P2P ARBITRAGE MONITOR</b>\n"
    msg += f"🕒 <i>{now}</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # ১. লাইভ মার্কেট আপডেট
    for method, data in market_data.items():
        msg += f"💳 <b>{method}</b>\n"
        if data.get("buy"):
            msg += "   🟢 Buy Ads:\n"
            for ad in data["buy"]:
                msg += f"      • <b>{ad['price']:.2f} BDT</b> | {ad['fee_status']} | {ad['merchant']}\n"
        if data.get("sell"):
            msg += "   🔴 Sell Ads:\n"
            for ad in data["sell"]:
                msg += f"      • <b>{ad['price']:.2f} BDT</b> | {ad['fee_status']} | {ad['merchant']}\n"
        msg += "\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    
    # ২. বিস্তারিত অ্যাকশন প্ল্যান (যা আপনি চেয়েছিলেন)
    if best and best["net_pct"] > 0:
        b = best["buy_data"]
        s = best["sell_data"]
        msg += "🎯 <b>BEST ACTION PLAN</b> 🎯\n\n"
        
        msg += f"🛒 <b>কোথা থেকে কিনবেন:</b>\n"
        msg += f"   <b>Binance</b> থেকে <b>{best['buy_method']}</b> দিয়ে\n"
        msg += f"   রেট: <b>{b['price']:.2f} BDT</b>\n"
        msg += f"   মার্চেন্ট: {b['merchant']}\n"
        msg += f"   চার্জ: {b['fee_status']}\n\n"
        
        msg += f"💰 <b>কোথায় বিক্রি করবেন:</b>\n"
        msg += f"   <b>Binance</b>-এ <b>{best['sell_method']}</b> দিয়ে\n"
        msg += f"   রেট: <b>{s['price']:.2f} BDT</b>\n"
        msg += f"   মার্চেন্ট: {s['merchant']}\n"
        msg += f"   চার্জ: {s['fee_status']}\n\n"

        msg += f"📈 <b>মোট লাভ:</b> <u>{best['net_pct']:.2f}%</u>\n"
        msg += f"💵 <b>টাকায় লাভ:</b> ~{best['profit_bdt']:.0f} BDT <i>(প্রতি {TRADE_SIZE_USDT:.0f} USDT-তে)</i>\n\n"
        
        if best["net_pct"] >= ALERT_THRESHOLD_PCT:
            msg += "🔥 <b>HIGH PROFIT ALERT! এখনই ট্রেড করুন!</b> 🔥\n"
        else:
            msg += "⚠️ <i>লাভ ১.৫% এর নিচে। ভালো স্প্রেডের জন্য অপেক্ষা করুন।</i>\n"
    else:
        msg += "❌ <i>এই মুহূর্তে লাভজনক কোনো সুযোগ পাওয়া যায়নি।</i>\n"
        
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "💡 <b>নিয়ম:</b> সবসময় 'Send Money' করবেন। কেউ এক্সট্রা চার্জ চাইলে অর্ডার ক্যানসেল করে দিন।"
    
    return msg

def main():
    log.info("Fetching data...")
    market_data = collect_market_data()
    best = find_best_opportunity(market_data)
    msg = format_full_analysis(market_data, best)
    send_telegram(msg)
    log.info("Update sent.")

if __name__ == "__main__":
    main()
