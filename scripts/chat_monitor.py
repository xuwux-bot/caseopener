#!/usr/bin/env python3
import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, Set, Optional, Tuple

import requests
from telegram import Update
from telegram.ext import ContextTypes

# ================= НАСТРОЙКИ =================
TOKEN = "Zluavtkju9WkqLYzGVKg"
DEFAULT_SENDER_ID = "EfezAdmin1"

CONFIG = {
    "UPDATE_INTERVAL": 2,
    "MAX_MESSAGES": 20,
    "SAVE_AS_JSON": True,
    "LOG_DIR": "logs",
    "API_BASE_URL": "https://api.efezgames.com/v1",
    "FIREBASE_URL": "https://api-project-7952672729.firebaseio.com",
    "REQUEST_TIMEOUT": 10,
    "RETRY_ATTEMPTS": 3,
    "RETRY_DELAY": 2,
    "DEFAULT_CHAT_REGION": "RU"
}
# ==============================================

monitoring_tasks: Dict[str, asyncio.Task] = {}
reply_map: Dict[int, Tuple[str, str]] = {}
sender_ids: Dict[int, str] = {}

# -------------------------------------------------------------------
# Вспомогательные функции
# -------------------------------------------------------------------

def get_log_path(channel: str) -> str:
    return os.path.join(CONFIG["LOG_DIR"], f"{channel}logs.json")

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
        r = requests.get(url, timeout=CONFIG["REQUEST_TIMEOUT"])
        r.raise_for_status()
        return str(r.json()["_id"])
    except:
        return "error: user not found or API error"

def _get_id_from_chat(keyword: str, chat_region: str) -> str:
    url = f"{CONFIG['FIREBASE_URL']}/Chat/Messages/{chat_region}.json?orderBy=\"ts\"&limitToLast=20"
    for attempt in range(CONFIG["RETRY_ATTEMPTS"]):
        try:
            r = requests.get(url, timeout=CONFIG["REQUEST_TIMEOUT"])
            messages = r.json()
            if not messages:
                return "error: no messages"
            for msg in messages.values():
                if (keyword.lower() in msg.get('msg', '').lower() or
                    keyword.lower() in msg.get('nick', '').lower()):
                    return msg.get('playerID', 'error: ID not found')
            return "error: user not found in last 20 messages"
        except Exception as e:
            if attempt < CONFIG["RETRY_ATTEMPTS"] - 1:
                time.sleep(CONFIG["RETRY_DELAY"])
                continue
            return f"error: {str(e)}"
    return "error: unknown"

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

def send_chat_message(sender_id: str, message: str, channel: str) -> bool:
    url = f"{CONFIG['API_BASE_URL']}/social/sendChat"
    params = {
        "token": TOKEN,
        "playerID": sender_id,
        "message": message,
        "channel": channel
    }
    try:
        resp = requests.get(url, params=params, timeout=CONFIG["REQUEST_TIMEOUT"])
        if resp.status_code == 200:
            return True
        else:
            print(f"Ошибка отправки в игру: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"Исключение при отправке: {e}")
        return False

# -------------------------------------------------------------------
# Мониторинг (фоновая задача)
# -------------------------------------------------------------------

async def monitor_worker(channel: str, bot, target_chat_id: int, task_name: str):
    seen_ids: Set[str] = set()
    log_path = get_log_path(channel)

    if CONFIG["SAVE_AS_JSON"] and os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                seen_ids.update(old_data.keys())
        except Exception as e:
            print(f"Не удалось прочитать {log_path}: {e}")

    await bot.send_message(
        chat_id=target_chat_id,
        text=f"📡 Мониторинг канала {channel} запущен. Новые сообщения будут появляться здесь.\n"
             f"Для остановки используй /stop {task_name}"
    )

    while True:
        try:
            url = f"{CONFIG['FIREBASE_URL']}/Chat/Messages/{channel}.json?orderBy=\"ts\"&limitToLast={CONFIG['MAX_MESSAGES']}"
            for attempt in range(CONFIG["RETRY_ATTEMPTS"]):
                try:
                    r = requests.get(url, timeout=CONFIG["REQUEST_TIMEOUT"])
                    messages = r.json()
                    break
                except Exception as e:
                    if attempt < CONFIG["RETRY_ATTEMPTS"] - 1:
                        await asyncio.sleep(CONFIG["RETRY_DELAY"])
                        continue
                    else:
                        raise e

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
                        reply_map[sent_msg.message_id] = (nick, channel)
                    except Exception as e:
                        print(f"Ошибка отправки в Telegram: {e}")

                    new_data[msg_id] = msg
                    seen_ids.add(msg_id)

            if CONFIG["SAVE_AS_JSON"] and new_data:
                if os.path.exists(log_path):
                    with open(log_path, 'r', encoding='utf-8') as f:
                        full_data = json.load(f)
                else:
                    full_data = {}
                full_data.update(new_data)
                with open(log_path, 'w', encoding='utf-8') as f:
                    json.dump(full_data, f, indent=2, ensure_ascii=False)

            await asyncio.sleep(CONFIG["UPDATE_INTERVAL"])
        except asyncio.CancelledError:
            await bot.send_message(
                chat_id=target_chat_id,
                text=f"🛑 Мониторинг канала {channel} остановлен."
            )
            break
        except Exception as e:
            print(f"Ошибка в мониторинге: {e}")
            await asyncio.sleep(5)

# -------------------------------------------------------------------
# Команды
# -------------------------------------------------------------------

async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE, active_tasks: dict):
    """Обработчик /monitor: без аргументов – справка, с аргументом – запуск."""
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        help_text = (
            "📡 Мониторинг чата Efez\n"
            "\n"
            "Эта функция позволяет в реальном времени получать сообщения из игрового чата.\n"
            "Доступные каналы: RU, US, PL, DE, PREMIUM, DEV, UA.\n"
            "\n"
            "Как использовать:\n"
            "• /monitor RU – запустить мониторинг канала RU\n"
            "• После запуска все новые сообщения будут приходить сюда\n"
            "• Ответь (reply) на любое сообщение бота, чтобы отправить ответ игроку\n"
            "• Просто напиши текст (без reply) – отправится как 'Offline mode msg:' в текущий канал\n"
            "• Остановить мониторинг: /stop Мониторинг RU\n"
            "\n"
            "Другие команды:\n"
            "/getid КАНАЛ ник – найти ID игрока по нику в указанном канале\n"
            "/getidchat КАНАЛ слово – найти ID по ключевому слову в сообщениях чата\n"
            "/setid новый_id – сменить ID отправителя\n"
            "/showid – показать текущий ID"
        )
        await update.message.reply_text(help_text)
        return

    channel = args[0].upper()
    allowed = ["RU", "US", "PL", "DE", "PREMIUM", "DEV", "UA"]
    if channel not in allowed:
        await update.message.reply_text(f"Неверный канал. Допустимы: {', '.join(allowed)}")
        return

    task_name = f"Мониторинг {channel}"
    if task_name in active_tasks and not active_tasks[task_name].done():
        await update.message.reply_text(f"⚠️ Мониторинг канала {channel} уже запущен.")
        return

    task = asyncio.create_task(
        monitor_worker(channel, context.bot, chat_id, task_name)
    )
    active_tasks[task_name] = task
    monitoring_tasks[task_name] = task

async def cmd_getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Формат: /getid КАНАЛ никнейм"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /getid КАНАЛ никнейм\nПример: /getid RU PlayerName")
        return

    channel = args[0].upper()
    allowed = ["RU", "US", "PL", "DE", "PREMIUM", "DEV", "UA"]
    if channel not in allowed:
        await update.message.reply_text(f"Неверный канал. Допустимы: {', '.join(allowed)}")
        return

    nickname = ' '.join(args[1:])
    await update.message.reply_text("🔍 Ищу...")
    result = get_user_id(nickname, channel)
    await update.message.reply_text(f"🔍 Результат: {result}")

async def cmd_getidchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Формат: /getidchat КАНАЛ ключевое_слово"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /getidchat КАНАЛ ключевое_слово\nПример: /getidchat RU привет")
        return

    channel = args[0].upper()
    allowed = ["RU", "US", "PL", "DE", "PREMIUM", "DEV", "UA"]
    if channel not in allowed:
        await update.message.reply_text(f"Неверный канал. Допустимы: {', '.join(allowed)}")
        return

    keyword = ' '.join(args[1:])
    await update.message.reply_text("🔍 Ищу...")
    result = get_user_id(None, channel, keyword=keyword)
    await update.message.reply_text(f"🔍 Результат: {result}")

async def cmd_setid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Укажи новый ID: /setid EfezAdmin1")
        return
    new_id = args[0]
    sender_ids[chat_id] = new_id
    await update.message.reply_text(f"✅ ID отправителя для этого чата изменён на: {new_id}")

async def cmd_showid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current = sender_ids.get(chat_id, DEFAULT_SENDER_ID)
    await update.message.reply_text(f"🆔 Текущий ID отправителя: {current}")

# -------------------------------------------------------------------
# Обработка ответов и офлайн-сообщений
# -------------------------------------------------------------------

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.message.reply_to_message:
        return False
    if update.message.reply_to_message.from_user.id != context.bot.id:
        return False

    replied_msg = update.message.reply_to_message
    if replied_msg.message_id not in reply_map:
        return False

    nick, channel = reply_map[replied_msg.message_id]
    user_text = update.message.text
    if not user_text:
        await update.message.reply_text("Нельзя отправить пустое сообщение.")
        return True

    chat_id = update.effective_chat.id
    sender_id = sender_ids.get(chat_id, DEFAULT_SENDER_ID)

    reply_text = f"Ответ игроку: {nick} - {user_text}"
    success = send_chat_message(sender_id, reply_text, channel)
    if success:
        await update.message.reply_text(f"✅ Ответ отправлен игроку {nick} в канал {channel}")
        del reply_map[replied_msg.message_id]
    else:
        await update.message.reply_text("❌ Не удалось отправить ответ в игру.")
    return True

async def handle_offline_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.message.reply_to_message:
        return False

    chat_id = update.effective_chat.id
    active_channel = None
    for name, task in monitoring_tasks.items():
        if not task.done() and name.startswith("Мониторинг "):
            active_channel = name.replace("Мониторинг ", "")
            break

    if not active_channel:
        return False

    user_text = update.message.text
    if not user_text:
        await update.message.reply_text("Нельзя отправить пустое сообщение.")
        return True

    sender_id = sender_ids.get(chat_id, DEFAULT_SENDER_ID)
    offline_msg = f"Offline mode msg: {user_text}"
    success = send_chat_message(sender_id, offline_msg, active_channel)
    if success:
        await update.message.reply_text(f"✅ Сообщение отправлено в канал {active_channel} (офлайн-режим)")
    else:
        await update.message.reply_text("❌ Не удалось отправить сообщение в игру.")
    return True
