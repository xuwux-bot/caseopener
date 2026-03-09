#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Optional, Dict, Set, Tuple

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ======================== НАСТРОЙКИ =========================
BOT_TOKEN = "8709948767:AAFP5CvMEHhismhq-onL1Tss26KlfgATkSc"
TOKEN = "Zluavtkju9WkqLYzGVKg"  # Токен для отправки сообщений в игру
DEFAULT_SENDER_ID = "EfezAdmin1"  # ID отправителя по умолчанию

CONFIG = {
    "DEFAULT_CHAT_REGION": "RU",
    "UPDATE_INTERVAL": 2,
    "MAX_MESSAGES": 20,
    "SAVE_AS_JSON": True,
    "JSON_LOG_PATH": "chat_messages.json",
    "API_BASE_URL": "https://api.efezgames.com/v1",
    "FIREBASE_URL": "https://api-project-7952672729.firebaseio.com"
}
# =============================================================

# Глобальные переменные
monitoring_active = False
monitoring_task = None
monitoring_chat_region = CONFIG["DEFAULT_CHAT_REGION"]
monitoring_bot = None
monitoring_target_chat = None
current_sender_id = DEFAULT_SENDER_ID

# Система доступа по паролю
PASSWORD = "201188messo"  # пароль по умолчанию
authorised_chats: Set[int] = set()  # ID чатов, которые ввели правильный пароль

# Словарь для связи message_id бота -> (ник, канал)
reply_map: Dict[int, Tuple[str, str]] = {}

def format_time(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(ts)

def _has_cyrillic(text: str) -> bool:
    return bool(re.search('[а-яА-Я]', text))

def _fetch_user_id(query: str) -> str:
    url = f"{CONFIG['API_BASE_URL']}/social/findUser?{query}"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return str(r.json()["_id"])
    except:
        return "error: user not found or API error"

def _get_id_from_chat(keyword: str, chat_region: str) -> str:
    url = f"{CONFIG['FIREBASE_URL']}/Chat/Messages/{chat_region}.json?orderBy=\"ts\"&limitToLast=20"
    try:
        r = requests.get(url, timeout=5)
        messages = r.json()
        if not messages:
            return "error: no messages"
        for msg in messages.values():
            if (keyword.lower() in msg.get('msg', '').lower() or
                keyword.lower() in msg.get('nick', '').lower()):
                return msg.get('playerID', 'error: ID not found')
        return "error: user not found in last 20 messages"
    except Exception as e:
        return f"error: {str(e)}"

def get_user_id(nickname: Optional[str], chat_region: str, keyword: Optional[str] = None) -> str:
    if keyword:
        return _get_id_from_chat(keyword, chat_region)

    if not nickname:
        return "error: no nickname provided"

    if nickname.startswith('#'):
        try:
            if len(nickname) < 7:
                return "error: invalid hash format"
            first = int(nickname[1:3], 16)
            second = int(nickname[3:5], 16)
            third = int(nickname[5:7], 16)
            numeric_id = str(first * 65536 + second * 256 + third)
            return _fetch_user_id(f"ID={numeric_id}")
        except:
            return "error: invalid hash format"

    if _has_cyrillic(nickname):
        try:
            import base64
            enc = base64.b64encode(nickname.encode()).decode()
            return _fetch_user_id(f"nick=@{enc}")
        except:
            return "error: encoding failed"

    return _fetch_user_id(f"nick={nickname}")

def check_player_by_id(player_id: str) -> Optional[Dict]:
    url = f"{CONFIG['API_BASE_URL']}/equipment/getEQ"
    params = {"playerID": player_id}
    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"error": f"HTTP {resp.status_code}", "response": resp.text}
    except Exception as e:
        return {"error": str(e)}

def send_chat_message(sender_id: str, message: str, channel: str) -> bool:
    """Отправляет сообщение в игровой чат через API"""
    url = f"{CONFIG['API_BASE_URL']}/social/sendChat"
    params = {
        "token": TOKEN,
        "playerID": sender_id,
        "message": message,
        "channel": channel
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            return True
        else:
            print(f"Ошибка отправки в игру: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"Исключение при отправке: {e}")
        return False

# ---------- Мониторинг чата ----------
async def monitor_worker(chat_region: str, bot, target_chat_id: int):
    global monitoring_active, reply_map
    seen_ids: Set[str] = set()
    json_path = CONFIG["JSON_LOG_PATH"]

    if CONFIG["SAVE_AS_JSON"] and os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                seen_ids.update(old_data.keys())
        except:
            pass

    while monitoring_active:
        try:
            url = f"{CONFIG['FIREBASE_URL']}/Chat/Messages/{chat_region}.json?orderBy=\"ts\"&limitToLast={CONFIG['MAX_MESSAGES']}"
            r = requests.get(url, timeout=5)
            messages = r.json()
            if not messages:
                await asyncio.sleep(CONFIG["UPDATE_INTERVAL"])
                continue

            sorted_msgs = sorted(messages.items(), key=lambda x: x[1].get('ts', 0))

            new_data = {}
            for msg_id, msg in sorted_msgs:
                if msg_id not in seen_ids:
                    ts = msg.get('ts', 0)
                    nick = msg.get('nick', '?')
                    text = msg.get('msg', '')
                    time_str = format_time(ts)
                    out = f"[{time_str}] [{nick}]: {text}"
                    
                    try:
                        sent_msg = await bot.send_message(chat_id=target_chat_id, text=out)
                        # Сохраняем связь: message_id бота -> (ник, канал)
                        reply_map[sent_msg.message_id] = (nick, chat_region)
                    except Exception as e:
                        print(f"Ошибка отправки в Telegram: {e}")

                    new_data[msg_id] = msg
                    seen_ids.add(msg_id)

            if CONFIG["SAVE_AS_JSON"] and new_data:
                if os.path.exists(json_path):
                    with open(json_path, 'r', encoding='utf-8') as f:
                        full_data = json.load(f)
                else:
                    full_data = {}
                full_data.update(new_data)
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(full_data, f, indent=2, ensure_ascii=False)

            await asyncio.sleep(CONFIG["UPDATE_INTERVAL"])
        except Exception as e:
            print(f"Ошибка в мониторинге: {e}")
            await asyncio.sleep(5)

# ---------- Обработчики команд с проверкой авторизации ----------
def authorized_only(func):
    """Декоратор для проверки авторизации чата перед выполнением команды."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id not in authorised_chats:
            await update.message.reply_text(
                "⛔ Доступ запрещён. Введи пароль для доступа к боту.\n"
                "Используй команду /login <пароль> или просто отправь пароль в чат."
            )
            return
        return await func(update, context)
    return wrapper

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in authorised_chats:
        await update.message.reply_text(
            "👋 Ты уже авторизован. Доступные команды:\n"
            "/monitor <канал> – начать мониторинг (RU, US, PL, DE, PREMIUM, DEV, UA)\n"
            "/stop – остановить мониторинг\n"
            "/status – текущий статус\n"
            "/getid <ник> – найти ID по нику\n"
            "/getidbykeyword <слово> – найти ID по ключевому слову в чате\n"
            "/checkid <ID> – проверить ID через API\n"
            "/changeid <новый ID> – сменить ID отправителя сообщений\n"
            "/showid – показать текущий ID отправителя\n"
            "/change_password <новый пароль> – сменить пароль доступа\n"
            "/password_show – показать текущий пароль\n"
            "/myid – узнать свой Telegram ID\n\n"
            "📝 Чтобы ответить игроку, просто ответь (reply) на его сообщение в этом чате."
        )
    else:
        await update.message.reply_text(
            "🔐 Для доступа к боту введи пароль.\n"
            "Используй команду /login <пароль> или просто отправь пароль в чат."
        )

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in authorised_chats:
        await update.message.reply_text("✅ Ты уже авторизован.")
        return
    if not context.args:
        await update.message.reply_text("Укажи пароль: /login <пароль>")
        return
    entered = ' '.join(context.args)
    if entered == PASSWORD:
        authorised_chats.add(chat_id)
        await update.message.reply_text("✅ Пароль верный! Доступ получен. Используй /start для списка команд.")
    else:
        await update.message.reply_text("❌ Неверный пароль. Попробуй ещё раз.")

@authorized_only
async def change_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PASSWORD
    if not context.args:
        await update.message.reply_text("Укажи новый пароль: /change_password <новый пароль>")
        return
    new_pass = ' '.join(context.args)
    PASSWORD = new_pass
    await update.message.reply_text("✅ Пароль успешно изменён.")

@authorized_only
async def password_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Показываем только авторизованным
    await update.message.reply_text(f"🔑 Текущий пароль: `{PASSWORD}`", parse_mode="Markdown")

@authorized_only
async def monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitoring_active, monitoring_task, monitoring_chat_region, monitoring_bot, monitoring_target_chat

    if not context.args:
        await update.message.reply_text("Укажи канал, например: /monitor RU")
        return

    channel = context.args[0].upper()
    allowed = ["RU", "US", "PL", "DE", "PREMIUM", "DEV", "UA"]
    if channel not in allowed:
        await update.message.reply_text(f"Неверный канал. Допустимы: {', '.join(allowed)}")
        return

    chat_id = update.effective_chat.id

    if monitoring_active and monitoring_task:
        monitoring_active = False
        monitoring_task.cancel()
        try:
            await monitoring_task
        except asyncio.CancelledError:
            pass

    monitoring_active = True
    monitoring_chat_region = channel
    monitoring_bot = context.bot
    monitoring_target_chat = chat_id
    
    monitoring_task = asyncio.create_task(
        monitor_worker(channel, context.bot, chat_id)
    )

    await update.message.reply_text(f"✅ Мониторинг канала {channel} запущен. Новые сообщения будут появляться здесь.")

@authorized_only
async def stop_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitoring_active, monitoring_task
    if monitoring_active and monitoring_task:
        monitoring_active = False
        monitoring_task.cancel()
        try:
            await monitoring_task
        except asyncio.CancelledError:
            pass
        await update.message.reply_text("🛑 Мониторинг остановлен.")
    else:
        await update.message.reply_text("Мониторинг не был запущен.")

@authorized_only
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if monitoring_active:
        await update.message.reply_text(f"📡 Мониторинг активен для канала: {monitoring_chat_region}")
    else:
        await update.message.reply_text("⏸ Мониторинг не запущен.")

@authorized_only
async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи никнейм, например: /getid EfezAdmin1")
        return
    nickname = ' '.join(context.args)
    region = monitoring_chat_region if monitoring_active else CONFIG["DEFAULT_CHAT_REGION"]
    result = get_user_id(nickname, region)
    await update.message.reply_text(f"🔍 Результат: {result}")

@authorized_only
async def getidbykeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи ключевое слово, например: /getidbykeyword привет")
        return
    keyword = ' '.join(context.args)
    region = monitoring_chat_region if monitoring_active else CONFIG["DEFAULT_CHAT_REGION"]
    result = get_user_id(None, region, keyword=keyword)
    await update.message.reply_text(f"🔍 Результат: {result}")

@authorized_only
async def checkid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи ID игрока, например: /checkid EfezAdmin1")
        return
    player_id = context.args[0]
    data = check_player_by_id(player_id)
    if data:
        text = json.dumps(data, indent=2, ensure_ascii=False)
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        await update.message.reply_text(f"📦 Ответ API:\n{text}")
    else:
        await update.message.reply_text("Не удалось получить данные.")

@authorized_only
async def changeid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_sender_id
    if not context.args:
        await update.message.reply_text("Укажи новый ID, например: /changeid a_12345")
        return
    new_id = context.args[0]
    current_sender_id = new_id
    await update.message.reply_text(f"✅ ID отправителя изменён на: {current_sender_id}")

@authorized_only
async def showid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Текущий ID отправителя: {current_sender_id}")

@authorized_only
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"Твой Telegram ID: `{user_id}`", parse_mode="Markdown")

# Обработчик текстовых сообщений (не команд) для авторизации и ответов
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    # Если чат ещё не авторизован, проверяем, не является ли сообщение паролем
    if chat_id not in authorised_chats:
        if text == PASSWORD:
            authorised_chats.add(chat_id)
            await update.message.reply_text("✅ Пароль верный! Доступ получен. Используй /start для списка команд.")
        else:
            await update.message.reply_text("❌ Неверный пароль. Попробуй ещё раз или используй /login.")
        return

    # Если авторизован, обрабатываем как возможный ответ на сообщение бота
    # Проверяем, есть ли reply
    if not update.message.reply_to_message:
        # Просто игнорируем обычные сообщения (не команды) если не reply
        return

    replied_msg = update.message.reply_to_message
    if replied_msg.from_user.id != context.bot.id:
        return

    if replied_msg.message_id not in reply_map:
        return

    nick, channel = reply_map[replied_msg.message_id]
    user_text = text
    if not user_text:
        await update.message.reply_text("Нельзя отправить пустое сообщение.")
        return

    reply_text = f"Ответ игроку: {nick} - {user_text}"
    success = send_chat_message(current_sender_id, reply_text, channel)
    if success:
        await update.message.reply_text(f"✅ Ответ отправлен игроку {nick} в канал {channel}")
        del reply_map[replied_msg.message_id]
    else:
        await update.message.reply_text("❌ Не удалось отправить ответ в игру. Проверь логи.")

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("login", login))
    application.add_handler(CommandHandler("change_password", change_password))
    application.add_handler(CommandHandler("password_show", password_show))
    application.add_handler(CommandHandler("monitor", monitor))
    application.add_handler(CommandHandler("stop", stop_monitor))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("getid", getid))
    application.add_handler(CommandHandler("getidbykeyword", getidbykeyword))
    application.add_handler(CommandHandler("checkid", checkid))
    application.add_handler(CommandHandler("changeid", changeid))
    application.add_handler(CommandHandler("showid", showid))
    application.add_handler(CommandHandler("myid", myid))

    # Обработчик всех текстовых сообщений (не команд)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
