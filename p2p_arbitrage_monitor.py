import os
import time
import logging
import requests
from datetime import datetime

# ============================ CONFIG ============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_THRESHOLD_PCT = 1.0      # ১% বা তার বেশি লাভ হলে অ্যালার্ট
MY_CAPITAL_BDT = 50000.0       # আপনার মোট পুঁজি (টাকা)
NETWORK_FEE_USDT = 0.2         # ক্রস-এক্সচেঞ্জে ডলার পাঠানোর ফি
REQUEST_TIMEOUT = 15
ROWS = 10                      

METHODS = ["bKash", "Nagad", "Bank Transfer"]
BINANCE_PAY = {"bKash": "bKash", "Nagad": "Nagad", "Bank Transfer": "BANK"}
BYBIT_PAY = {"bKash": "14", "Nagad": "15", "Bank Transfer": "16"} 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Content-Type": "application/json",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p2p")

FEE_KEYWORDS = ["charge", "fee", "extra", "20tk", "20 taka", "15tk", "15 taka", "cash out", "cashout"]

def analyze_fee_status(remarks):
    if not remarks: return "✅ No Fee Mentioned"
    remarks_lower = remarks.lower()
    if any(keyword in remarks_lower for keyword in FEE_KEYWORDS):
        return "🚨 Hidden Charge"
    return "✅ No Fee Mentioned"

# ========================== FETCHERS ==========================
def _fits(min_bdt, max_bdt, price):
    try:
        size_bdt = MY_CAPITAL_BDT
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
                advertiser = item.get("advertiser", {})
                merchant = advertiser.get("nickName", "Unknown")
                remarks = adv.get("remarks", "") or ""
                ads.append({"price": p, "merchant": merchant, "fee_status": analyze_fee_status(remarks)})
        return ads
    except Exception as e:
        return []

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
        ads = []
        for it in (r.json().get("result") or {}).get("items") or []:
            p = float(it["price"])
            if _fits(it.get("minAmount"), it.get("maxAmount"), p):
                ads.append({"price": p, "merchant": "Bybit User", "fee_status": "✅ No Fee Mentioned"})
        return ads
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
                ads.append({"price": p, "merchant": "Bitget User", "fee_status": "✅ No Fee Mentioned"})
        return ads
    except Exception:
        return []

# ============================ CORE LOGIC ============================
def collect_market_data():
    market_data = {}
    exchanges = {"Binance": fetch_binance, "Bybit": fetch_bybit, "Bitget": fetch_bitget}
    
    for ex_name, fetch_func in exchanges.items():
        for m in METHODS:
            buy_ads = fetch_func(m, "buy")
            sell_ads = fetch_func(m, "sell")
            if buy_ads or sell_ads:
                market_data.setdefault(ex_name, {})[m] = {
                    "buy": sorted(buy_ads, key=lambda x: x["price"])[:2] if buy_ads else [],
                    "sell": sorted(sell_ads, key=lambda x: x["price"], reverse=True)[:2] if sell_ads else []
                }
            time.sleep(0.5)
    return market_data

def find_best_opportunity(market_data):
    best = None
    for buy_ex, methods_buy in market_data.items():
        for buy_method, data_buy in methods_buy.items():
            if not data_buy.get("buy"): continue
            
            for sell_ex, methods_sell in market_data.items():
                for sell_method, data_sell in methods_sell.items():
                    if not data_sell.get("sell"): continue
                    
                    buy_price = data_buy["buy"][0]["price"]
                    sell_price = data_sell["sell"][0]["price"]
                    
                    # আমরা যদি একই এক্সচেঞ্জে কিনে বেচি, ফি লাগবে না। ভিন্ন এক্সচেঞ্জে হলে ফি লাগবে।
                    is_same_exchange = (buy_ex == sell_ex)
                    fee_bdt = 0 if is_same_exchange else (NETWORK_FEE_USDT * sell_price)
                    
                    # ৫০,০০০ টাকা দিয়ে কত ডলার কেনা যাবে
                    usdt_amount = (MY_CAPITAL_BDT - fee_bdt) / buy_price
                    revenue_bdt = usdt_amount * sell_price
                    
                    net_profit_bdt = revenue_bdt - MY_CAPITAL_BDT
                    net_pct = (net_profit_bdt / MY_CAPITAL_BDT) * 100
                    
                    if best is None or net_pct > best["net_pct"]:
                        best = {
                            "buy_ex": buy_ex, "buy_method": buy_method, "buy_price": buy_price, "buy_data": data_buy["buy"][0],
                            "sell_ex": sell_ex, "sell_method": sell_method, "sell_price": sell_price, "sell_data": data_sell["sell"][0],
                            "is_same_ex": is_same_exchange,
                            "net_pct": net_pct, 
                            "profit_bdt": net_profit_bdt,
                            "usdt_amount": usdt_amount
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
    
    msg = "🧑‍💼 <b>PROFESSIONAL P2P MANAGER</b>\n"
    msg += f"🕒 <i>{now}</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if best:
        b = best["buy_data"]
        s = best["sell_data"]
        
        msg += "📊 <b>১. পুঁজি ও লাভের হিসাব</b>\n"
        msg += f"   • আপনার পুঁজি: <b>{MY_CAPITAL_BDT:.0f} BDT</b>\n"
        msg += f"   • আপনার লাভ: <b>{best['net_pct']:.2f}%</b>\n"
        msg += f"   • টাকায় লাভ: <b>~{best['profit_bdt']:.0f} BDT</b>\n\n"
        
        if best["is_same_ex"]:
            msg += f"🤝 <b>ধরন: Same Exchange Arbitrage</b>\n\n"
        else:
            msg += f"🚀 <b>ধরন: Cross-Exchange Arbitrage</b>\n"
            msg += f"   (এক্সচেঞ্জের মধ্যে ডলার পাঠানোর ফি বাদ দেওয়া হয়েছে)\n\n"

        msg += "🛒 <b>২. BUY AD (কেনার বিজ্ঞাপন)</b>\n"
        msg += f"   • এক্সচেঞ্জ: <b>{best['buy_ex']}</b>\n"
        msg += f"   • রেট: <b>{best['buy_price']:.2f} BDT</b>\n"
        msg += f"   • পরিমাণ: <b>{best['usdt_amount']:.2f} USDT</b>\n"
        msg += f"   • লিমিট: <b>৫,০০০ - {MY_CAPITAL_BDT:.0f} BDT</b>\n"
        msg += f"   • পেমেন্ট: {best['buy_method']}\n"
        msg += f"   • মার্চেন্ট: {b['merchant']} ({b['fee_status']})\n\n"
        
        msg += "💰 <b>৩. SELL AD (বেচার বিজ্ঞাপন)</b>\n"
        msg += f"   • এক্সচেঞ্জ: <b>{best['sell_ex']}</b>\n"
        msg += f"   • রেট: <b>{best['sell_price']:.2f} BDT</b>\n"
        msg += f"   • পেমেন্ট: {best['sell_method']}\n"
        msg += f"   • মার্চেন্ট: {s['merchant']} ({s['fee_status']})\n\n"

        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "💡 <b>ম্যানেজার'স অ্যাডভাইস:</b>\n"
        if best["is_same_ex"]:
            msg += "একই অ্যাপে কেনাবেচা করলে ডলার ট্রান্সফার করতে হবে না, তাই রিস্ক কম। রেট আপডেট করে অ্যাড পোস্ট করুন।\n"
        else:
            msg += f"{best['buy_ex']} থেকে কিনে {best['sell_ex']}-এ বিক্রি করলে বেশি লাভ হবে। তবে ডলার পাঠানোর সময় BEP20 নেটওয়ার্ক ব্যবহার করবেন।\n"
    else:
        msg += "❌ <i>এই মুহূর্তে লাভজনক কোনো সুযোগ পাওয়া যায়নি।</i>\n\n"
        
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "🛡️ <b>PROBLEM SOLVER</b>\n"
    msg += "• এক্সট্রা চার্জ চাইলে: অর্ডার বাতিল করুন।\n"
    msg += "• ফেক স্ক্রিনশট দিলে: নিজের লাইভ ব্যালেন্স চেক করুন।"
        
    return msg

def main():
    log.info("Fetching data from Binance, Bybit, Bitget...")
    market_data = collect_market_data()
    best = find_best_opportunity(market_data)
    msg = format_full_analysis(market_data, best)
    send_telegram(msg)
    log.info("Update sent.")

if __name__ == "__main__":
    main()
