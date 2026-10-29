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

# ========================== FEE DETECTOR ==========================
FEE_KEYWORDS = ["charge", "fee", "extra", "20tk", "20 taka", "15tk", "15 taka", "cash out", "cashout"]

def check_remarks(remarks):
    if not remarks: return "✅ No Fee Mentioned"
    remarks_lower = remarks.lower()
    if any(keyword in remarks_lower for keyword in FEE_KEYWORDS):
        return "🚨 Fee May Apply"
    return "✅ No Fee Mentioned"

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
    try:
        r = requests.post(url, json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        ads = []
        for item in r.json().get("data") or []:
            adv = item["adv"]
            p = float(adv["price"])
            if _fits(adv.get("minSingleTransAmount"), adv.get("maxSingleTransAmount"), p):
                # মার্চেন্টের আসল নাম বের করার সঠিক নিয়ম
                advertiser = adv.get("advertiser", {})
                merchant_name = advertiser.get("nickName", "Unknown")
                remarks = adv.get("remarks", "") or ""
                
                ads.append({
                    "price": p,
                    "merchant": merchant_name,
                    "fee_status": check_remarks(remarks)
                })
        return ads
    except Exception as e:
        log.warning(f"Binance/{method} failed: {e}")
        return []

# ============================ CORE LOGIC ============================
def find_best_opportunity():
    best = None
    for buy_method in METHODS:
        buy_ads = fetch_binance(buy_method, "buy")
        if not buy_ads: continue
        best_buy = min(buy_ads, key=lambda x: x["price"])
        
        for sell_method in METHODS:
            sell_ads = fetch_binance(sell_method, "sell")
            if not sell_ads: continue
            best_sell = max(sell_ads, key=lambda x: x["price"])
            
            if best_sell["price"] <= best_buy["price"]: continue
            
            net_pct = ((best_sell["price"] - best_buy["price"]) / best_buy["price"]) * 100
            
            if best is None or net_pct > best["net_pct"]:
                best = {
                    "buy_method": buy_method, "buy_data": best_buy,
                    "sell_method": sell_method, "sell_data": best_sell,
                    "net_pct": net_pct, 
                    "profit_bdt": (best_sell["price"] - best_buy["price"]) * TRADE_SIZE_USDT
                }
        time.sleep(0.5)
    return best

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

def format_message(best):
    now = datetime.now().strftime("%I:%M %p")
    msg = f"📊 <b>P2P Arbitrage ({now})</b>\n\n"
    
    if best and best["net_pct"] > 0:
        b = best["buy_data"]
        s = best["sell_data"]
        
        msg += f"🟢 <b>BUY</b> from <b>{best['buy_method']}</b>\n"
        msg += f"   Rate: <b>{b['price']:.2f} BDT</b>\n"
        msg += f"   Merchant: {b['merchant']}\n"
        msg += f"   Fee: {b['fee_status']}\n\n"
            
        msg += f"🔴 <b>SELL</b> to <b>{best['sell_method']}</b>\n"
        msg += f"   Rate: <b>{s['price']:.2f} BDT</b>\n"
        msg += f"   Merchant: {s['merchant']}\n"
        msg += f"   Fee: {s['fee_status']}\n\n"

        msg += f"📈 <b>Net Profit:</b> <u>{best['net_pct']:.2f}%</u>\n"
        msg += f"💵 <b>Est. Profit:</b> ~{best['profit_bdt']:.0f} BDT <i>(on {TRADE_SIZE_USDT:.0f} USDT)</i>\n\n"
        
        msg += "💡 <i>Tip: Use 'Send Money' only. Cancel if merchant asks for extra fee.</i>\n"
        
        if best["net_pct"] >= ALERT_THRESHOLD_PCT:
            msg += "\n🔥 <b>HIGH PROFIT ALERT! EXECUTE!</b>"
        else:
            msg += "\n⚠️ <i>Below 1.5% target. Wait for better spread.</i>"
    else:
        msg += "❌ <i>No profitable opportunities found right now.</i>"
        
    return msg

def main():
    log.info("Fetching data...")
    best = find_best_opportunity()
    msg = format_message(best)
    send_telegram(msg)
    log.info("Update sent.")

if __name__ == "__main__":
    main()
