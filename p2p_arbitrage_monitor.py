def format_full_analysis(market_data, best):
    now = datetime.now().strftime("%I:%M %p, %d %b %Y")
    
    msg = "👑 <b>MAKER MODE: P2P ADVISOR</b> 👑\n"
    msg += f"🕒 <i>{now}</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if best:
        b = best["buy_data"]
        s = best["sell_data"]
        
        # মেকারদের জন্য দাম অ্যাডজাস্ট করা (যাতে দ্রুত অর্ডার পায়)
        # আপনি যদি বাই অ্যাড দেন, তবে বাজারের সেরা দামের চেয়ে ০.০৫ বেশি দিন
        maker_buy_price = b['price'] + 0.05 
        # আপনি যদি সেল অ্যাড দেন, তবে বাজারের সেরা দামের চেয়ে ০.০৫ কম দিন
        maker_sell_price = s['price'] - 0.05
        
        actual_profit = maker_sell_price - maker_buy_price
        profit_pct = (actual_profit / maker_buy_price) * 100
        profit_bdt = actual_profit * TRADE_SIZE_USDT

        msg += "🎯 <b>আপনার বিজ্ঞাপন দেওয়ার সঠিক রেট:</b>\n\n"
        
        msg += f"🛒 <b>BUY AD (কেনার বিজ্ঞাপন)</b>\n"
        msg += f"   রেট: <b>{maker_buy_price:.2f} BDT</b>\n"
        msg += f"   পেমেন্ট: {best['buy_method']}\n"
        msg += f"   <i>(বাজারে এখন সেরা দাম: {b['price']:.2f})</i>\n\n"
        
        msg += f"💰 <b>SELL AD (বেচার বিজ্ঞাপন)</b>\n"
        msg += f"   রেট: <b>{maker_sell_price:.2f} BDT</b>\n"
        msg += f"   পেমেন্ট: {best['sell_method']}\n"
        msg += f"   <i>(বাজারে এখন সেরা দাম: {s['price']:.2f})</i>\n\n"

        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"📈 <b>আপনার নিট লাভ:</b> {profit_pct:.2f}%\n"
        msg += f"💵 <b>টাকায় লাভ:</b> ~{profit_bdt:.0f} BDT <i>(প্রতি {TRADE_SIZE_USDT:.0f} USDT-তে)</i>\n\n"
        
        msg += "💡 <b>করণীয়:</b> এই দাম দুটি দেখে আপনি এখনই অ্যাপে গিয়ে ২টি আলাদা বিজ্ঞাপন পোস্ট করুন (১টি Buy, ১টি Sell)।"
    else:
        msg += "❌ <i>এই মুহূর্তে লাভজনক কোনো সুযোগ পাওয়া যায়নি।</i>\n"
        
    return msgdef main():
    log.info("Fetching data...")
    
    # ১. টেস্ট মেসেজ পাঠানো (যদি এটাও না আসে, বুঝবেন Token বা Chat ID ভুল)
    log.info("Sending test message...")
    send_telegram("✅ বট সফলভাবে চালু হয়েছে! এখন আমি মার্কেট স্ক্যান করছি...")
    
    # ২. মূল কাজ
    market_data = collect_market_data()
    best = find_best_opportunity(market_data)
    msg = format_full_analysis(market_data, best)
    send_telegram(msg)
    log.info("Update sent.")

if __name__ == "__main__":
    main()
