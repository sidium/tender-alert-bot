# bot.py — TenderAlertBot: RSS + кнопки + БЕЗ ОШИБОК
import asyncio
import sqlite3
import requests
import feedparser
import os
from datetime import datetime, timedelta, timezone
from lxml import etree
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = 900  # 15 минут
RSS_URL = "https://zakupki.gov.ru/epz/order/notice/rss"
DB_PATH = "tenders.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === КНОПКИ ===
def main_menu():
    keyboard = [
        [KeyboardButton(text="Добавить фильтр")],
        [KeyboardButton(text="Мои подписки")],
        [KeyboardButton(text="Отписаться")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=False)

def region_menu():
    keyboard = [
        [InlineKeyboardButton(text="Москва", callback_data="region_Москва")],
        [InlineKeyboardButton(text="Санкт-Петербург", callback_data="region_Санкт-Петербург")],
        [InlineKeyboardButton(text="Все регионы", callback_data="region_Все")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def price_menu():
    keyboard = [
        [InlineKeyboardButton(text="До 1 млн", callback_data="price_1000000")],
        [InlineKeyboardButton(text="До 5 млн", callback_data="price_5000000")],
        [InlineKeyboardButton(text="Без ограничения", callback_data="price_0")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# === БАЗА ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tenders (
        id TEXT PRIMARY KEY,
        title TEXT,
        price REAL,
        region TEXT,
        url TEXT,
        pub_date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        keywords TEXT,
        region TEXT,
        max_price REAL
    )''')
    conn.commit()
    conn.close()

# === RSS + ПАРСИНГ ===
def get_new_tenders_from_rss():
    feed = feedparser.parse(RSS_URL)
    new_tenders = []
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()

    for entry in feed.entries:
        pub_date = entry.get('published_parsed')
        if not pub_date: continue
        pub_iso = datetime(*pub_date[:6], tzinfo=timezone.utc).isoformat()
        if pub_iso < cutoff: continue

        reg_num_match = entry.link.split('regNumber=')
        if len(reg_num_match) < 2: continue
        reg_num = reg_num_match[1].split('&')[0]

        new_tenders.append({
            'id': reg_num,
            'title': entry.title,
            'url': entry.link,
            'pub_date': pub_iso
        })
    return new_tenders

def fetch_and_parse_tender(tender):
    try:
        xml_url = tender['url'].replace('/view/', '/viewXml/')
        response = requests.get(xml_url, timeout=10)
        if response.status_code != 200: return None

        root = etree.fromstring(response.content)
        ns = {'ns2': 'http://zakupki.gov.ru/oos/export/1'}
        
        price_elem = root.find('.//ns2:initialSum', ns)
        price = float(price_elem.text) if price_elem is not None and price_elem.text else 0
        
        region_elem = root.find('.//ns:customer/ns:fullName', ns)
        region = region_elem.text if region_elem is not None else ""

        return {
            'id': tender['id'],
            'title': tender['title'],
            'price': price,
            'region': region,
            'url': tender['url'],
            'pub_date': tender['pub_date']
        }
    except Exception as e:
        print(f"Ошибка парсинга {tender['id']}: {e}")
        return None

# === ПРОВЕРКА ТЕНДЕРОВ ===
async def check_tenders():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Проверка...")
    new_items = get_new_tenders_from_rss()
    if not new_items: return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        for item in new_items:
            c.execute("SELECT 1 FROM tenders WHERE id = ?", (item['id'],))
            if c.fetchone(): continue

            full_tender = fetch_and_parse_tender(item)
            if not full_tender: continue

            c.execute("""INSERT INTO tenders (id, title, price, region, url, pub_date)
                         VALUES (?, ?, ?, ?, ?, ?)""",
                      (full_tender['id'], full_tender['title'], full_tender['price'],
                       full_tender['region'], full_tender['url'], full_tender['pub_date']))

            c.execute("SELECT user_id, keywords, region, max_price FROM users WHERE keywords IS NOT NULL")
            users = c.fetchall()
            for user_id, keywords, region_filter, max_price in users:
                kw_list = [k.strip().lower() for k in keywords.split(',') if k.strip()]
                if not any(k in full_tender['title'].lower() for k in kw_list): continue
                if region_filter and region_filter != "Все" and region_filter.lower() not in full_tender['region'].lower(): continue
                if max_price and full_tender['price'] > max_price: continue

                msg = f"""
НОВЫЙ ТЕНДЕР!
*{full_tender['title']}*
Цена: {full_tender['price']:,.0f} ₽
Регион: {full_tender['region'] or '—'}
[Открыть]({full_tender['url']})
                """
                try:
                    await bot.send_message(user_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
                except: pass
        conn.commit()
    except Exception as e:
        print(f"Ошибка в check_tenders: {e}")
    finally:
        conn.close()

# === КОМАНДЫ ===
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я @TenderAlertBot\n"
        "Настраивай уведомления о тендерах по 44-ФЗ:",
        reply_markup=main_menu()
    )

@dp.message(F.text == "Добавить фильтр")
async def add_filter(message: types.Message):
    await message.answer("Введи ключевые слова (через запятую):")
    dp["pending_keywords"] = dp.get("pending_keywords", {})
    dp["pending_keywords"][message.from_user.id] = True

@dp.message(lambda m: dp.get("pending_keywords", {}).get(m.from_user.id))
async def get_keywords(message: types.Message):
    keywords = message.text.strip()
    if not keywords:
        await message.answer("Ошибка! Напиши слова.", reply_markup=main_menu())
        return

    user_id = message.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        c.execute("INSERT OR REPLACE INTO users (user_id, keywords) VALUES (?, ?)", (user_id, keywords))
        conn.commit()
    except Exception as e:
        print(f"Ошибка сохранения ключей: {e}")
    finally:
        conn.close()

    dp["pending_keywords"].pop(user_id, None)
    await message.answer(f"Ключи: *{keywords}*\nВыбери регион:", parse_mode="Markdown", reply_markup=region_menu())

@dp.callback_query(F.data.startswith("region_"))
async def set_region(callback: types.CallbackQuery):
    region = callback.data.split("_", 1)[1]
    user_id = callback.from_user.id

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        c.execute("UPDATE users SET region = ? WHERE user_id = ?", (region, user_id))
        conn.commit()
    except Exception as e:
        print(f"Ошибка сохранения региона: {e}")
    finally:
        conn.close()

    await callback.message.edit_text(f"Регион: *{region}*\nВыбери цену:", parse_mode="Markdown", reply_markup=price_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("price_"))
async def set_price(callback: types.CallbackQuery):
    price = int(callback.data.split("_", 1)[1])
    user_id = callback.from_user.id

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        max_price = price if price > 0 else None
        c.execute("UPDATE users SET max_price = ? WHERE user_id = ?", (max_price, user_id))

        c.execute("SELECT keywords, region FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        keywords = row[0] if row else "—"
        region = row[1] if row and row[1] else "Все"
        price_text = f"до {price:,} ₽" if price > 0 else "без ограничения"

        conn.commit()

        # УДАЛЁН reply_markup=main_menu()
        await callback.message.edit_text(
            f"Подписка готова!\n"
            f"Ключи: *{keywords}*\n"
            f"Регион: *{region}*\n"
            f"Цена: *{price_text}*",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Ошибка в set_price: {e}")
        try:
            await callback.message.edit_text("Ошибка. Попробуй снова.")
        except: pass
    finally:
        conn.close()

    await callback.answer()

@dp.message(F.text == "Мои подписки")
async def my_subs(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        c.execute("SELECT keywords, region, max_price FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row or not row[0]:
            await message.answer("У тебя нет подписок.", reply_markup=main_menu())
            return
        price = f"до {int(row[2]):,} ₽" if row[2] else "без ограничения"
        await message.answer(
            f"Твои фильтры:\n"
            f"Ключи: *{row[0]}*\n"
            f"Регион: *{row[1] or 'Все'}*\n"
            f"Цена: *{price}*",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
    except Exception as e:
        print(f"Ошибка в my_subs: {e}")
    finally:
        conn.close()

@dp.message(F.text == "Отписаться")
async def unsubscribe(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception as e:
        print(f"Ошибка в отписке: {e}")
    finally:
        conn.close()

    await message.answer("Подписка удалена.", reply_markup=main_menu())

# === ЗАПУСК ===
async def on_startup(dispatcher):
    asyncio.create_task(scheduler())

async def scheduler():
    while True:
        await check_tenders()
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    init_db()
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
