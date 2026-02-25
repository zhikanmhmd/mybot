import os
import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
from datetime import datetime
import time
import re
from urllib.parse import urljoin
from requests.exceptions import RequestException
from fake_useragent import UserAgent

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from groq import Groq

# تنظیمات
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALANCHAND_TOKEN = "9jkzPPBrUV6NpVYxPHO4"  # این توکن منقضی شده، اما نگه داشته شده

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)

ua = UserAgent()

# ────────────────────────────────────────────────
# تبدیل اعداد فارسی/عربی به لاتین (برای یکسان شدن فونت در تلگرام)
# ────────────────────────────────────────────────

def to_latin_digits(text: str) -> str:
    if not text or not text.strip().isdigit():
        return text
    persian_to_latin = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
    return text.translate(persian_to_latin)

# ────────────────────────────────────────────────
# توابع قیمت دلار و تتر
# ────────────────────────────────────────────────

def get_dollar_alanchand() -> str:
    url = "https://alanchand.com/"
    try:
        headers = {"User-Agent": ua.random}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return "ناموجود"
        soup = BeautifulSoup(res.text, "html.parser")
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 3 and "دلار آمریکا" in cells[0].text:
                    sell_price = cells[2].text.strip().replace(",", "")
                    return to_latin_digits(sell_price)
        return "ناموجود"
    except Exception as e:
        print(f"خطا در اسکریپینگ دلار: {e}")
        return "خطا"


def get_tether_nobitex() -> str:
    url = "https://nobitex.ir/price/usdt"
    
    try:
        headers = {
            "User-Agent": ua.random or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code != 200:
            return f"ناموجود (کد: {res.status_code})"
        
        soup = BeautifulSoup(res.text, "html.parser")
        
        # اولویت ۱: جستجو برای متن‌هایی که مستقیماً قیمت رو دارن
        keywords = ["قیمت لحظه‌ای", "قیمت فعلی", "آخرین قیمت", "قیمت تومانی", "تتر", "USDT"]
        for elem in soup.find_all(['span', 'div', 'p', 'strong', 'h2', 'td']):
            text = elem.get_text(strip=True)
            if any(kw in text for kw in keywords):
                match = re.search(r'(\d{1,3}(?:[,\s٬]\d{3})*)\s*(?:IRT|تومان| تومان|$|IRT)', text)
                if match:
                    price_str = match.group(1).replace(',', '').replace('٬', '').replace(' ', '')
                    if len(price_str) in (6, 7):
                        return to_latin_digits(price_str)
        
        # اولویت ۲: اعداد ۶-۷ رقمی در محدوده منطقی
        all_text = soup.get_text(separator=" ", strip=True)
        matches = re.findall(r'\b(\d{6,7})\b', all_text)
        if matches:
            for m in matches:
                if 140000 <= int(m) <= 200000:
                    return to_latin_digits(m)
        
        return "ناموجود (قیمت مناسب پیدا نشد)"
    
    except Exception as e:
        print(f"خطا در نوبیتکس: {str(e)[:150]}")
        return "خطا در اتصال"


def get_prices() -> tuple:
    dollar = get_dollar_alanchand()
    tether = get_tether_nobitex()
    return dollar, tether


# ────────────────────────────────────────────────
# توابع دیگر (عکس، خلاصه، تیترها)
# ────────────────────────────────────────────────

def escape_markdown_v2(text: str) -> str:
    if not text:
        return ""
    reserved = r'\_*[]()~`>#+-=|{}.!'
    for char in reserved:
        text = text.replace(char, '\\' + char)
    return text


def get_article_image(url: str) -> str | None:
    try:
        headers = {"User-Agent": ua.random or "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            src = og["content"].strip()
            if src.startswith("//"): src = "https:" + src
            if src.startswith("/"): src = urljoin(url, src)
            if any(ext in src.lower() for ext in [".jpg",".jpeg",".png",".webp",".gif"]):
                return src

        twitter = soup.find("meta", attrs={"name": "twitter:image"})
        if twitter and twitter.get("content"):
            src = twitter["content"].strip()
            if src.startswith("//"): src = "https:" + src
            if src.startswith("/"): src = urljoin(url, src)
            if any(ext in src.lower() for ext in [".jpg",".jpeg",".png",".webp"]):
                return src

        for sel in [
            "img.size-full", "img.aligncenter", ".featured-image", ".post-thumbnail",
            ".entry-content img", ".content img", "figure img", "article img"
        ]:
            img = soup.select_one(sel)
            if img:
                for attr in ["src", "data-src", "data-lazy-src", "data-original"]:
                    src = img.get(attr)
                    if src:
                        if src.startswith("//"): src = "https:" + src
                        if src.startswith("/"): src = urljoin(url, src)
                        if any(ext in src.lower() for ext in [".jpg",".jpeg",".png",".webp"]) and "logo" not in src and "avatar" not in src:
                            return src

        return None

    except Exception as e:
        print(f"خطا عکس {url}: {str(e)[:100]}")
        return None


async def generate_summary(url: str) -> str:
    if not url or not url.startswith(('http://', 'https://')):
        return "لینک نامعتبر"

    try:
        headers = {"User-Agent": ua.random or "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return f"خطا {response.status_code}"

        soup = BeautifulSoup(response.text, "html.parser")
        content = ""
        for cls in ['content','post-content','entry-content','article-body','news-content','body']:
            div = soup.find(['div','article'], class_=cls)
            if div:
                content = div.get_text(separator="\n", strip=True)
                break

        if len(content) < 200:
            ps = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 30]
            content = "\n".join(ps)

        if len(content) < 100:
            return "محتوای کافی نبود"

        content = content[:7000]

        completion = GROQ_CLIENT.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "فقط یک خلاصه کوتاه و دقیق (۱ تا ۲ جمله) به فارسی بنویس. رسمی و روان. اگر متن کافی نبود بنویس: محتوای کافی نبود."},
                {"role": "user", "content": content}
            ],
            max_tokens=120,
            temperature=0.35,
        )

        summary_text = completion.choices[0].message.content.strip()
        return summary_text if summary_text and len(summary_text) >= 10 else "خلاصه تولید نشد"

    except Exception as ex:
        print(f"خطا summarize {url}: {str(ex)[:150]}")
        return "خلاصه در دسترس نیست"


def get_economic_headlines(limit=5):
    url = "https://zoomon.ir/"
    headers = {"User-Agent": ua.random or "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(response.text, "html.parser")
        headlines = []
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            if len(text) > 20 and 'مطالعه' not in text and 'کامنت' not in text:
                href = a['href']
                if href.startswith('/'): href = "https://zoomon.ir" + href
                if href.startswith('http'):
                    headlines.append((text, href))
                    if len(headlines) >= limit: break
        return headlines[:limit] or [("تیتر جدیدی پیدا نشد.", None)]
    except Exception as e:
        print(f"خطا تیتر اقتصادی: {e}")
        return [("خطا در بارگیری تیترها", None)]


def get_zoomit_tech_headlines(limit=5):
    url = "https://www.zoomit.ir/"
    headers = {"User-Agent": ua.random or "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(response.text, "html.parser")
        headlines = []
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            if len(text) > 25 and any(kw in text.lower() for kw in ['لو رفت','معرفی','هوش','سامسونگ','اپل','گوشی','لپ','تکنولوژی']):
                href = a['href']
                if href.startswith('/'): href = "https://www.zoomit.ir" + href
                if href.startswith('https://www.zoomit.ir/'):
                    headlines.append((text, href))
                    if len(headlines) >= limit: break
        return headlines[:limit] or [("تیتر جدیدی پیدا نشد.", None)]
    except Exception as e:
        print(f"خطا تیتر زومیت: {e}")
        return [("خطا در بارگیری اخبار زومیت", None)]


def get_zoomg_cinema_game_headlines(limit=5):
    url = "https://www.zoomg.ir/"
    headers = {"User-Agent": ua.random or "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(response.text, "html.parser")
        headlines = []
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            if len(text) > 25 and any(kw in text.lower() for kw in ['فیلم','بازی','سینما','گیم','سریال','مایکروسافت','نقد','تریلر']):
                href = a['href']
                if href.startswith('/'): href = "https://www.zoomg.ir" + href
                if href.startswith('https://www.zoomg.ir/'):
                    headlines.append((text, href))
                    if len(headlines) >= limit: break
        return headlines[:limit] or [("تیتر جدیدی پیدا نشد.", None)]
    except Exception as e:
        print(f"خطا تیتر زومجی: {e}")
        return [("خطا در بارگیری اخبار زومجی", None)]


# ────────────────────────────────────────────────
# کیبورد اصلی
# ────────────────────────────────────────────────

def get_main_reply_keyboard():
    keyboard = [
        [KeyboardButton("💰 قیمت فعلی دلار و تتر"),     KeyboardButton("⏰ تنظیم ارسال خودکار")],
        [KeyboardButton("📰 تیترهای اقتصادی امروز"),   KeyboardButton("🖥️ اخبار تکنولوژی")],
        [KeyboardButton("🎮🎬 سینما و گیم"),             KeyboardButton("🛑 لغو همه اعلان‌ها")],
        [KeyboardButton("ℹ️ راهنما"),                    KeyboardButton("🔄 شروع دوباره")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="یکی رو انتخاب کن..."
    )


# ────────────────────────────────────────────────
# کیبورد بازه زمانی
# ────────────────────────────────────────────────

def get_interval_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("هر ۵ دقیقه", callback_data="interval_5m")],
        [InlineKeyboardButton("هر ۱ ساعت",   callback_data="interval_1h")],
        [InlineKeyboardButton("هر ۳ ساعت",   callback_data="interval_3h")],
        [InlineKeyboardButton("هر ۶ ساعت",   callback_data="interval_6h")],
        [InlineKeyboardButton("بازگشت",      callback_data="back")],
    ])


# ────────────────────────────────────────────────
# ارسال قیمت دوره‌ای
# ────────────────────────────────────────────────

async def send_price(context: ContextTypes.DEFAULT_TYPE):
    dollar, tether = get_prices()
    now = datetime.now(ZoneInfo('Asia/Tehran')).strftime('%H:%M')
    text = f"🪙 قیمت لحظه‌ای ({now})\n\n💰 دلار: {dollar} تومان\n🔗 تتر: {tether} تومان"
    await context.bot.send_message(chat_id=context.job.chat_id, text=text, reply_markup=get_main_reply_keyboard())


# ────────────────────────────────────────────────
# هندلر اصلی
# ────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    async def send_news_section(section_title: str, items_getter, emoji: str):
        items = items_getter(5)
        if not items or all("خطا" in t[0] for t in items):
            await update.message.reply_text("هیچ خبری یافت نشد.", reply_markup=get_main_reply_keyboard())
            return

        output = escape_markdown_v2(f"{emoji} {section_title}:\n\n")
        photo_sent = False

        for i, (title, url) in enumerate(items, 1):
            photo_url = get_article_image(url)
            summary = await generate_summary(url)
            summary_line = "خلاصه: در دسترس نبود" if any(w in summary.lower() for w in ["خطا","نیست","نشد"]) else f"خلاصه: {summary}"
            link_line = f"لینک: {url}" if url else ""

            if not photo_sent and photo_url:
                caption = f"{i}. {title}\n{summary_line}\n{link_line}"
                try:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_url,
                        caption=escape_markdown_v2(caption[:900]),
                        parse_mode="MarkdownV2",
                        reply_markup=get_main_reply_keyboard()
                    )
                    photo_sent = True
                    continue
                except Exception as e:
                    print(f"خطا ارسال عکس {url}: {str(e)[:100]}")

            output += f"{i}\. {escape_markdown_v2(title)}\n"
            output += escape_markdown_v2(f"   {summary_line}\n")
            if url:
                output += escape_markdown_v2(f"   لینک: {url}\n")
            output += "\n"

        await update.message.reply_text(
            output,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
            reply_markup=get_main_reply_keyboard()
        )

    # تشخیص دکمه‌ها
    if any(w in text for w in ["قیمت فعلی", "دلار", "تتر", "💰"]):
        dollar, tether = get_prices()
        now = datetime.now(ZoneInfo('Asia/Tehran')).strftime('%H:%M')
        msg = f"🪙 قیمت لحظه‌ای ({now})\nدلار: {dollar} تومان\nتتر: {tether} تومان"
        await update.message.reply_text(msg, reply_markup=get_main_reply_keyboard())

    elif any(w in text for w in ["تنظیم ارسال", "خودکار", "⏰"]):
        await update.message.reply_text("بازه زمانی رو انتخاب کن:", reply_markup=get_interval_keyboard())

    elif any(w in text for w in ["اقتصادی", "📰"]):
        await send_news_section("تیترهای اقتصادی امروز", get_economic_headlines, "📰")

    elif any(w in text for w in ["تکنولوژی", "زومیت", "🖥️"]):
        await send_news_section("اخبار تکنولوژی", get_zoomit_tech_headlines, "🖥️")

    elif any(w in text for w in ["سینما", "گیم", "زومجی", "🎮", "🎬"]):
        await send_news_section("اخبار سینما و گیم", get_zoomg_cinema_game_headlines, "🎮🎬")

    elif any(w in text for w in ["لغو", "🛑"]):
        for job in context.job_queue.get_jobs_by_name(f"price_{chat_id}"):
            job.schedule_removal()
        await update.message.reply_text("✓ همه اعلان‌ها لغو شد", reply_markup=get_main_reply_keyboard())

    elif any(w in text for w in ["راهنما", "ℹ️"]):
        await update.message.reply_text(
            "راهنما:\n"
            "💰 قیمت فعلی → قیمت لحظه‌ای\n"
            "⏰ تنظیم ارسال → قیمت دوره‌ای\n"
            "📰 اقتصادی → اخبار اقتصادی (با عکس خبر اول)\n"
            "🖥️ تکنولوژی → اخبار زومیت (با عکس خبر اول)\n"
            "🎮🎬 سینما و گیم → اخبار زومجی (با عکس خبر اول)\n"
            "🛑 لغو → قطع اعلان‌ها\n"
            "🔄 شروع دوباره → ریست بات\n\n"
            "سوال داری مستقیم بنویس!",
            reply_markup=get_main_reply_keyboard()
        )

    elif any(w in text for w in ["شروع دوباره", "ری استارت", "🔄"]):
        for job in context.job_queue.get_jobs_by_name(f"price_{chat_id}"):
            job.schedule_removal()
        await update.message.reply_text(
            "بات دوباره راه‌اندازی شد! 🌱\nاز دکمه‌های پایین انتخاب کن:",
            reply_markup=get_main_reply_keyboard()
        )

    else:
        await update.message.reply_text("لطفاً از دکمه‌های پایین استفاده کن 😊", reply_markup=get_main_reply_keyboard())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! به ربات خوش آمدی 🌟\nاز دکمه‌های پایین انتخاب کن:",
        reply_markup=get_main_reply_keyboard()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.from_user.id

    if data == "back":
        await query.edit_message_text("بازگشت", reply_markup=None)
        await context.bot.send_message(chat_id, "منوی اصلی:", reply_markup=get_main_reply_keyboard())
        return

    if data.startswith("interval_"):
        val = data.split("_")[1]
        mapping = {"5m": (300, "۵ دقیقه"), "1h": (3600, "۱ ساعت"), "3h": (10800, "۳ ساعت"), "6h": (21600, "۶ ساعت")}
        if val not in mapping: return
        sec, disp = mapping[val]

        for job in context.job_queue.get_jobs_by_name(f"price_{chat_id}"):
            job.schedule_removal()

        context.job_queue.run_repeating(
            send_price,
            interval=sec,
            first=15,
            chat_id=chat_id,
            name=f"price_{chat_id}",
        )

        await query.edit_message_text(f"✓ تنظیم شد: هر {disp}", reply_markup=None)


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("بات شروع شد.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()