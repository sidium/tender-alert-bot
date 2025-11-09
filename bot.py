# bot.py
import asyncio
import logging
import sqlite3
import requests
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram import F

# === НАСТРОЙКИ ===
BOT_TOKEN = "ВАШ_ТОКЕН_ОТ_BOTFATHER"  # ← Замени!
API_KEY = "ВАШ_API_КЛЮЧ_С_ZAKUPKI.GOV.RU"  # ← Замени!
API_URL = "https://api.zakupki.gov.ru/v1/search"
CHECK_INTERVAL = 900  # 15 минут

# === Логи ===
logging.basicConfig(level=logging.INFO)

# === Инициализация ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === База данных ===
def init_db():
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            keywords TEXT,
            region TEXT,
            max_price REAL,
            last_checked TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    conn.commit()
    conn.close()

# === API запрос к zakupki.gov.ru ===
def search_tenders(keywords, region, max_price):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {
        "query": keywords,
        "pageSize": 10,
        "pageNumber": 1,
        "sortBy": "publicationDate",
        "sortDirection": "desc"
    }
    if region:
        params["region"] = region
    if max_price:
        params["priceTo"] = max_price

    try:
        response = requests.get(API_URL, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get("items", [])
    except:
        pass
    return []

# === Проверка новых тендеров ===
async def check_new_tenders():
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("SELECT * FROM filters")
    filters = c.fetchall()

    for f in filters:
        filter_id, user_id, keywords, region, max_price, last_checked = f
        tenders = search_tenders(keywords, region, max_price)

        for tender in tenders:
            pub_date = tender.get("publicationDate", "")
            if not pub_date or not last_checked:
                continue
            if datetime.fromisoformat(pub_date.replace("Z", "+00:00")) > datetime.fromisoformat(last_checked):
                # Новое уведомление
                msg = f"""
НОВЫЙ ТЕНДЕР!
*{tender.get('title', 'Без названия')}*
Цена: {tender.get('initialPrice', '—')} ₽
Регион: {tender.get('region', '—')}
Опубликовано: {pub_date[:10]}
[Открыть в ЕИС]({tender.get('url', 'https://zakupki.gov.ru')})
                """
                try:
                    await bot.send_message(user_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
                except:
                    pass  # пользователь заблокировал бота

        # Обновляем время проверки
        c.execute("UPDATE filters SET last_checked = ? WHERE id = ?", (datetime.utcnow().isoformat(), filter_id))
    conn.commit()
    conn.close()

# === Фоновый таск ===
async def scheduler():
    while True:
        await check_new_tenders()
        await asyncio.sleep(CHECK_INTERVAL)

# === Команды бота ===
@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"

    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

    await message.answer(
        "Привет! Я @TenderAlertBot\n\n"
        "Отправь мне:\n"
        "• Ключевые слова (например: *ноутбук*)\n"
        "• Регион (например: *Москва*)\n"
        "• Макс. цена (например: *1000000*)\n\n"
        "Формат: `ноутбук Москва 1500000`",
        parse_mode="Markdown"
    )

@dp.message(F.text)
async def set_filter(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    parts = text.split()
    if len(parts) < 1:
        await message.answer("Напиши хотя бы ключевые слова!")
        return

    keywords = parts[0]
    region = parts[1] if len(parts) > 1 else None
    max_price = float(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("INSERT INTO filters (user_id, keywords, region, max_price, last_checked) VALUES (?, ?, ?, ?, ?)",
              (user_id, keywords, region, max_price, (datetime.utcnow() - timedelta(minutes=1)).isoformat()))
    conn.commit()
    conn.close()

    await message.answer(
        f"Подписка создана!\n"
        f"Ключи: *{keywords}*\n"
        f"Регион: {region or '—'}\n"
        f"Цена до: {max_price or '—'} ₽\n\n"
        f"Уведомления каждые 15 минут.",
        parse_mode="Markdown"
    )

# === Запуск ===
async def main():
    init_db()
    dp.startup.register(lambda: asyncio.create_task(scheduler()))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())