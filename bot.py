# bot.py — УСКОРЕННАЯ ВЕРСИЯ С ФИЛЬТРАМИ
import asyncio
import sqlite3
import requests
import zipfile
import os
import re
from datetime import datetime
from lxml import etree
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = 1800  # 30 минут
XML_URL = "https://ftp.zakupki.gov.ru/out/published/44fz/notices/notice_current.zip"
ZIP_PATH = "/tmp/notices.zip"
EXTRACT_DIR = "/tmp/notices/"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === База ===
def init_db():
    conn = sqlite3.connect('tenders.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS seen_tenders (id TEXT PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        keywords TEXT,
        region TEXT,
        max_price REAL
    )''')
    conn.commit()
    conn.close()

# === Скачать XML (только если нужно) ===
def download_xml():
    if os.path.exists(ZIP_PATH):
        return  # Уже скачан
    print("Скачиваю XML...")
    r = requests.get(XML_URL, auth=('pro-zakupki', 'pro-zakupki'), stream=True, timeout=60)
    with open(ZIP_PATH, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

# === Распаковать выборочно (только нужные XML) ===
def extract_needed_files(keywords, region, max_price):
    needed = []
    with zipfile.ZipFile(ZIP_PATH) as z:
        for name in z.namelist():
            if not name.endswith('.xml'): continue
            # Читаем только заголовок XML (первые 2 КБ)
            with z.open(name) as f:
                head = f.read(2048).decode('utf-8', errors='ignore')
                if keywords and not any(k.lower() in head.lower() for k in keywords.split()):
                    continue
                if region and region not in head:
                    continue
                needed.append(name)
    # Распаковываем только нужные
    with zipfile.ZipFile(ZIP_PATH) as z:
        for name in needed[:50]:  # Лимит 50 файлов за раз
            z.extract(name, EXTRACT_DIR)
    return [os.path.join(EXTRACT_DIR, n) for n in needed[:50]]

# === Парсинг одного тендера (быстрый) ===
def parse_tender(xml_path):
    try:
        tree = etree.parse(xml_path)
        root = tree.getroot()
        ns = {'ns2': 'http://zakupki.gov.ru/oos/export/1'}

        reg_num = root.find('.//ns2:regNum', ns)
        if not reg_num: return None
        tender_id = reg_num.text

        name = root.find('.//ns2:name', ns)
        price_elem = root.find('.//ns2:initialSum', ns)
        price = float(price_elem.text) if price_elem is not None else 0

        return {
            'id': tender_id,
            'title': name.text if name else "—",
            'price': price,
            'url': f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_id}"
        }
    except:
        return None

# === Проверка новых тендеров ===
async def check_tenders():
    conn = sqlite3.connect('tenders.db')
    c = conn.cursor()
    c.execute("SELECT user_id, keywords, region, max_price FROM users")
    users = c.fetchall()

    if not users:
        conn.close()
        return

    download_xml()

    for user_id, keywords, region, max_price in users:
        if not keywords: continue
        keywords_list = [k.strip() for k in keywords.split(',') if k.strip()]

        # Извлекаем только релевантные XML
        xml_files = extract_needed_files(keywords, region, max_price)

        for xml_path in xml_files:
            tender = parse_tender(xml_path)
            if not tender: continue

            # Фильтры
            if max_price and tender['price'] > max_price:
                continue
            if region and region not in tender['title']:
                continue

            # Уже видели?
            c.execute("SELECT 1 FROM seen_tenders WHERE id = ?", (tender['id'],))
            if c.fetchone(): continue

            # Сохранить
            c.execute("INSERT INTO seen_tenders (id) VALUES (?)", (tender['id'],))

            msg = f"""
НОВЫЙ ТЕНДЕР!
*{tender['title']}*
Цена: {tender['price']:,.0f} ₽
[Открыть]({tender['url']})
            """
            try:
                await bot.send_message(user_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
            except:
                pass

    conn.commit()
    conn.close()

# === Команды ===
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я ищу тендеры по 44-ФЗ\n\n"
        "Формат: `ноутбук, принтер, Москва, 2000000`\n"
        "• Ключи через запятую\n"
        "• Регион (по желанию)\n"
        "• Макс. цена (по желанию)\n\n"
        "Пример: `ремонт дорог, Санкт-Петербург, 5000000`"
    )

@dp.message(F.text)
async def set_filter(message: types.Message):
    user_id = message.from_user.id
    parts = [p.strip() for p in message.text.split(',')]
    keywords = parts[0] if parts else ""
    region = parts[1] if len(parts) > 1 else None
    max_price = float(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

    if not keywords:
        await message.answer("Напиши хотя бы ключевые слова!")
        return

    conn = sqlite3.connect('tenders.db')
    c = conn.cursor()
    c.execute("REPLACE INTO users (user_id, keywords, region, max_price) VALUES (?, ?, ?, ?)",
              (user_id, keywords, region, max_price))
    conn.commit()
    conn.close()

    await message.answer(
        f"Подписка обновлена!\n"
        f"Ключи: *{keywords}*\n"
        f"Регион: {region or '—'}\n"
        f"Цена до: {max_price or '—'} ₽",
        parse_mode="Markdown"
    )

# === Запуск ===
async def main():
    init_db()
    if not os.path.exists(EXTRACT_DIR):
        os.makedirs(EXTRACT_DIR)
    dp.startup.register(lambda: asyncio.create_task(scheduler()))
    await dp.start_polling(bot)

async def scheduler():
    while True:
        await check_tenders()
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
