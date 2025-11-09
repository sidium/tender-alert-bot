# bot.py — TenderAlertBot с фильтрами, XML, ускорением и исправленным запуском
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

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")  # ОБЯЗАТЕЛЬНО добавь в Railway Variables!
CHECK_INTERVAL = 1800  # 30 минут
XML_URL = "https://ftp.zakupki.gov.ru/out/published/44fz/notices/notice_current.zip"
ZIP_PATH = "/tmp/notices.zip"
EXTRACT_DIR = "/tmp/notices/"

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ ===
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

# === СКАЧАТЬ XML (только если нет) ===
def download_xml():
    if os.path.exists(ZIP_PATH):
        return
    print("Скачивание XML...")
    try:
        r = requests.get(XML_URL, auth=('pro-zakupki', 'pro-zakupki'), stream=True, timeout=60)
        r.raise_for_status()
        with open(ZIP_PATH, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
    except Exception as e:
        print(f"Ошибка скачивания: {e}")

# === ФИЛЬТРАЦИЯ XML БЕЗ РАСПАКОВКИ (ускорение) ===
def get_relevant_xml_paths(keywords, region, max_price):
    if not os.path.exists(ZIP_PATH):
        return []
    
    relevant = []
    keywords_list = [k.strip().lower() for k in keywords.split(',') if k.strip()]
    
    with zipfile.ZipFile(ZIP_PATH) as z:
        for name in z.namelist():
            if not name.endswith('.xml'):
                continue
            try:
                with z.open(name) as f:
                    head = f.read(2048).decode('utf-8', errors='ignore').lower()
                    if keywords_list and not any(k in head for k in keywords_list):
                        continue
                    if region and region.lower() not in head:
                        continue
                    relevant.append(name)
            except:
                continue
    return relevant[:50]  # Ограничиваем 50 файлов

# === РАСПАКОВКА ТОЛЬКО НУЖНЫХ ФАЙЛОВ ===
def extract_files(file_list):
    if not file_list:
        return []
    with zipfile.ZipFile(ZIP_PATH) as z:
        for name in file_list:
            z.extract(name, EXTRACT_DIR)
    return [os.path.join(EXTRACT_DIR, name) for name in file_list]

# === ПАРСИНГ ОДНОГО ТЕНДЕРА ===
def parse_tender(xml_path):
    try:
        tree = etree.parse(xml_path)
        root = tree.getroot()
        ns = {'ns2': 'http://zakupki.gov.ru/oos/export/1'}

        reg_num = root.find('.//ns2:regNum', ns)
        if not reg_num or not reg_num.text:
            return None
        tender_id = reg_num.text

        name_elem = root.find('.//ns2:name', ns)
        price_elem = root.find('.//ns2:initialSum', ns)
        price = float(price_elem.text) if price_elem is not None and price_elem.text else 0

        return {
            'id': tender_id,
            'title': name_elem.text if name_elem is not None else "—",
            'price': price,
            'url': f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_id}"
        }
    except Exception as e:
        print(f"Ошибка парсинга {xml_path}: {e}")
        return None

# === ПРОВЕРКА НОВЫХ ТЕНДЕРОВ ===
async def check_tenders():
    conn = sqlite3.connect('tenders.db')
    c = conn.cursor()
    c.execute("SELECT user_id, keywords, region, max_price FROM users WHERE keywords IS NOT NULL")
    users = c.fetchall()
    if not users:
        conn.close()
        return

    download_xml()
    for user_id, keywords, region, max_price in users:
        xml_names = get_relevant_xml_paths(keywords, region, max_price)
        xml_paths = extract_files(xml_names)

        for xml_path in xml_paths:
            tender = parse_tender(xml_path)
            if not tender:
                continue

            # Фильтр по цене
            if max_price and tender['price'] > max_price:
                continue

            # Уже видели?
            c.execute("SELECT 1 FROM seen_tenders WHERE id = ?", (tender['id'],))
            if c.fetchone():
                continue

            # Сохраняем
            c.execute("INSERT INTO seen_tenders (id) VALUES (?)", (tender['id'],))

            # Уведомление
            msg = f"""
НОВЫЙ ТЕНДЕР!
*{tender['title']}*
Цена: {tender['price']:,.0f} ₽
[Открыть]({tender['url']})
            """
            try:
                await bot.send_message(user_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
            except:
                pass  # пользователь заблокировал

    conn.commit()
    conn.close()

# === КОМАНДЫ ===
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я @TenderAlertBot — ищу тендеры по 44-ФЗ\n\n"
        "Формат: `ноутбук, принтер, Москва, 2000000`\n"
        "• Ключи через запятую\n"
        "• Регион (по желанию)\n"
        "• Макс. цена (по желанию)\n\n"
        "Пример: `ремонт дорог, Санкт-Петербург, 5000000`",
        parse_mode="Markdown"
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

# === ЗАПУСК (ИСПРАВЛЕННЫЙ) ===
async def on_startup(dispatcher):
    asyncio.create_task(scheduler())

async def scheduler():
    while True:
        await check_tenders()
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    init_db()
    if not os.path.exists(EXTRACT_DIR):
        os.makedirs(EXTRACT_DIR)
    
    dp.startup.register(on_startup)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
