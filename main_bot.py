#!/usr/bin/env python3
import asyncio
import os
from typing import Set, Dict, List

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from scripts.chat_monitor import (
    cmd_monitor,
    cmd_getid,
    cmd_getidchat,
    cmd_setid,
    cmd_showid,
    handle_reply,
    handle_offline_message
)

from scripts.spam_bot import (
    handle_spam_command,
    handle_spam_dialog_entry,
    stop_spam_task,
    get_active_spam_tasks
)

BOT_TOKEN = "8709948767:AAFP5CvMEHhismhq-onL1Tss26KlfgATkSc"
PASSWORD = "201188messo"
OWNER_ID = 5150403377

authorised_chats: Set[int] = set()
active_tasks: Dict[str, asyncio.Task] = {}

def is_authorized(chat_id: int, user_id: int = None) -> bool:
    if chat_id in authorised_chats:
        return True
    if user_id and user_id == OWNER_ID:
        authorised_chats.add(chat_id)
        return True
    return False

def format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names)-1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if is_authorized(chat_id, user_id):
        await update.message.reply_text("👋 Ты уже авторизован. Используй /help для списка команд.")
    else:
        await update.message.reply_text("🔐 Для доступа к боту введи пароль.\nИспользуй /login <пароль> или просто отправь пароль в чат.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return
    text = (
        "📋 Доступные команды:\n\n"
        "/monitor <канал> – запустить мониторинг (или /monitor для справки)\n"
        "/getid КАНАЛ ник – найти ID игрока по нику в указанном канале\n"
        "/getidchat КАНАЛ слово – найти ID по ключевому слову в чате\n"
        "/setid <новый ID> – сменить ID отправителя\n"
        "/showid – показать текущий ID\n"
        "/logsfile – показать список файлов логов\n"
        "/logsfile download КАНАЛ – скачать лог указанного канала\n"
        "/logsfile download all – скачать все файлы логов\n"
        "/spam – справка по спам-боту\n"
        "/spam start КАНАЛ – начать настройку спама для канала\n"
        "/spam stop КАНАЛ – остановить спам в канале\n"
        "/spam status – показать статус каналов\n"
        "/tasks – показать активные задачи\n"
        "/stop <имя> – остановить задачу (например /stop Мониторинг RU или /stop Спам RU)\n"
        "/setpass <новый пароль> – сменить пароль (только для владельца)\n"
        "/help – это сообщение"
    )
    await update.message.reply_text(text)

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if is_authorized(chat_id, user_id):
        await update.message.reply_text("✅ Ты уже авторизован.")
        return
    if not context.args:
        await update.message.reply_text("Укажи пароль: /login <пароль>")
        return
    entered = ' '.join(context.args)
    if entered == PASSWORD:
        authorised_chats.add(chat_id)
        await update.message.reply_text("✅ Пароль верный! Доступ получен.")
    else:
        await update.message.reply_text("❌ Неверный пароль.")

async def setpass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ Только владелец может менять пароль.")
        return
    if not context.args:
        await update.message.reply_text("Укажи новый пароль: /setpass <пароль>")
        return
    global PASSWORD
    PASSWORD = ' '.join(context.args)
    await update.message.reply_text("✅ Пароль изменён.")

async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return
    await cmd_monitor(update, context, active_tasks)

async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return
    await cmd_getid(update, context)

async def getidchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return
    await cmd_getidchat(update, context)

async def setid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return
    await cmd_setid(update, context)

async def showid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return
    await cmd_showid(update, context)

async def logsfile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return

    os.makedirs("logs", exist_ok=True)
    args = context.args

    if not args:
        files = []
        try:
            for f in os.listdir("logs"):
                if f.endswith("logs.json") and os.path.isfile(os.path.join("logs", f)):
                    size = os.path.getsize(os.path.join("logs", f))
                    files.append((f, size))
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка чтения папки logs: {e}")
            return

        if not files:
            await update.message.reply_text("📭 В папке logs нет файлов логов.")
            return

        text = "📁 **Файлы логов:**\n\n"
        for f, size in sorted(files):
            text += f"• `{f}` – {format_size(size)}\n"
        text += "\nЧтобы скачать файл: `/logsfile download КАНАЛ`\nНапример: `/logsfile download RU`\nЧтобы скачать всё: `/logsfile download all`"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    if args[0].lower() == "download":
        if len(args) < 2:
            await update.message.reply_text("Укажи канал для скачивания: `/logsfile download RU` или `/logsfile download all`", parse_mode="Markdown")
            return

        target = args[1].upper()
        if target == "ALL":
            files = []
            try:
                for f in os.listdir("logs"):
                    if f.endswith("logs.json") and os.path.isfile(os.path.join("logs", f)):
                        files.append(f)
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка чтения папки logs: {e}")
                return

            if not files:
                await update.message.reply_text("📭 Нет файлов для скачивания.")
                return

            await update.message.reply_text(f"📦 Начинаю отправку {len(files)} файлов...")
            for f in sorted(files):
                file_path = os.path.join("logs", f)
                try:
                    with open(file_path, "rb") as doc:
                        await context.bot.send_document(chat_id=chat_id, document=doc, filename=f)
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка при отправке {f}: {e}")
                await asyncio.sleep(1)
            return

        allowed_channels = ["RU", "US", "PL", "DE", "PREMIUM", "DEV", "UA"]
        if target not in allowed_channels:
            await update.message.reply_text(f"Неверный канал. Допустимы: {', '.join(allowed_channels)}")
            return

        filename = f"{target}logs.json"
        file_path = os.path.join("logs", filename)
        if not os.path.exists(file_path):
            await update.message.reply_text(f"❌ Файл {filename} не найден.")
            return

        try:
            with open(file_path, "rb") as doc:
                await context.bot.send_document(chat_id=chat_id, document=doc, filename=filename)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при отправке файла: {e}")
        return

    await update.message.reply_text("Использование: /logsfile (список) или /logsfile download КАНАЛ/all")

async def spam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return
    await handle_spam_command(update, context)

async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return

    text = "📌 **Активные задачи:**\n"
    has_tasks = False

    for name, task in active_tasks.items():
        if not task.done():
            text += f"• {name}: 🔴 работает\n"
            has_tasks = True

    for ch in get_active_spam_tasks():
        text += f"• Спам {ch}: 🔴 работает\n"
        has_tasks = True

    if not has_tasks:
        text = "📭 Нет активных задач."
    await update.message.reply_text(text, parse_mode="Markdown")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not is_authorized(chat_id, user_id):
        await update.message.reply_text("⛔ Сначала авторизуйся.")
        return

    if not context.args:
        await update.message.reply_text("Укажи имя задачи: /stop <имя> (например /stop Мониторинг RU или /stop Спам RU)")
        return

    task_name = ' '.join(context.args)

    # Проверяем, не спам ли это
    if task_name.startswith("Спам "):
        channel = task_name[5:].strip()
        if stop_spam_task(channel):
            await update.message.reply_text(f"✅ Спам в канал {channel} остановлен.")
        else:
            await update.message.reply_text(f"❌ Спам в канал {channel} не запущен.")
        return

    # Иначе ищем среди задач мониторинга
    if task_name not in active_tasks:
        await update.message.reply_text(f"❌ Задача '{task_name}' не найдена.")
        return

    task = active_tasks[task_name]
    if not task.done():
        task.cancel()
        await update.message.reply_text(f"✅ Задача '{task_name}' остановлена.")
    else:
        await update.message.reply_text(f"⚠️ Задача '{task_name}' уже завершена.")
    if task_name in active_tasks:
        del active_tasks[task_name]

# ============= ИСПРАВЛЕННЫЙ ОБРАБОТЧИК =============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = update.message.text

    # 1. Сначала проверяем авторизацию
    if not is_authorized(chat_id, user_id):
        if text == PASSWORD:
            authorised_chats.add(chat_id)
            await update.message.reply_text("✅ Пароль верный! Доступ получен.")
        else:
            await update.message.reply_text("❌ Неверный пароль. Попробуй ещё раз или используй /login.")
        return

    # 2. Если авторизован, обрабатываем сообщения модулями
    if await handle_reply(update, context):
        return
    if await handle_offline_message(update, context):
        return
    if await handle_spam_dialog_entry(update, context):
        return

    # 3. Если ничего не подошло – игнорируем
    # (можно ничего не делать или отправить справку)
# ==================================================

def main():
    os.makedirs("logs", exist_ok=True)
    os.makedirs("spam_data", exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("setpass", setpass))

    app.add_handler(CommandHandler("monitor", monitor_command))
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("getidchat", getidchat_command))
    app.add_handler(CommandHandler("setid", setid_command))
    app.add_handler(CommandHandler("showid", showid_command))
    app.add_handler(CommandHandler("logsfile", logsfile_command))
    app.add_handler(CommandHandler("spam", spam_command))

    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("stop", stop_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Главный бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()
