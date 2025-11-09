# bot.py — ЧАСТОЕ ОБНОВЛЕНИЕ + RSS + КЕШ
import asyncio
import sqlite3
import requests
import feedparser
import os
from datetime import datetime, timedelta
from lxml import etree
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = 900  # 15 минут
RSS_URL = "https://zakupki.gov.ru/epz/order/notice/rss"
DB_PATH = "tenders.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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

# === ПОЛУЧИТЬ НОВЫЕ ТЕНДЕРЫ ЧЕРЕЗ RSS ===
def get_new_tenders_from_rss():
    feed = feedparser.parse(RSS_URL)
    new_tenders = []
    cutoff = (datetime.utcnow() - timedelta(minutes=20)).isoformat()  # последние 20 мин

    for entry in feed.entries:
        pub_date = entry.get('published_parsed')
        if not pub_date: continue
        pub_iso = datetime(*pub_date[:6]).isoformat()
        if pub_iso < cutoff: continue

        reg_num = entry.link.split('regNumber=')[-1].split('&')[0]
        url = entry.link
        title = entry.title
        new_tenders.append({
            'id': reg_num,
            'title': title,
            'url': url,
            'pub_date': pub_iso
        })
    return new_tenders

# === СКАЧАТЬ И СПАРСИТЬ ОДИН ТЕНДЕР ПО URL ===
def fetch_and_parse_tender(tender):
    try:
        xml_url = tender['url'].replace('/view/', '/viewXml/')
        response = requests.get(xml_url, timeout=10)
        if response.status_code != 200: return None

        root = etree.fromstring(response.content)
        ns = {'ns2': 'http://zakupki.gov.ru/oos/export/1'}
        
        price_elem = root.find('.//ns2:initialSum', ns)
        price = float(price_elem.text) if price_elem is not None else 0
        region = root.find('.//ns:customer/ns:fullName', ns)
        region_text = region.text if region is not None else ""

        return {
            'id': tender['id'],
            'title': tender['title'],
            'price': price,
            'region': region_text,
            'url': tender['url'],
            'pub_date': tender['pub_date']
        }
    except:
        return None

# === ПРОВЕРКА НОВЫХ ТЕНДЕРОВ ===
async def check_tenders():
    print("Проверка новых тендеров через RSS...")
    new_items = get_new_tenders_from_rss()
    if not new_items:
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    for item in new_items:
        # Проверяем, уже есть?
        c.execute("SELECT 1 FROM tenders WHERE id = ?", (item['id'],))
        if c.fetchone(): continue

        # Парсим детали
        full_tender = fetch_and_parse_tender(item)
        if not full_tender: continue

        # Сохраняем
        c.execute("""INSERT INTO tenders (id, title, price, region, url, pub_date)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (full_tender['id'], full_tender['title'], full_tender['price'],
                   full_tender['region'], full_tender['url'], full_tender['pub_date']))

        # Рассылаем по фильтрам
        c.execute("SELECT user_id, keywords, region, max_price FROM users")
        users = c.fetchall()
        for user_id, keywords, region_filter, max_price in users:
            if not keywords: continue
            kw_list = [k.strip().lower() for k in keywords.split(',')]
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
    conn.close()

# === КНОПКИ (как раньше) ===
# ... (вставь из предыдущей версии: main_menu, region_menu, price_menu, команды)

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
