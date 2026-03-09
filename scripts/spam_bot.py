#!/usr/bin/env python3
import asyncio
import csv
import os
import random
import secrets
import string
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
from telegram import Update
from telegram.ext import ContextTypes

# ================= НАСТРОЙКИ =================
SPAM_DATA_DIR = "spam_data"
MAX_WORKERS = 50
SOURCE_DESCRIPTIONS = {
    'RUaccount': 'Россия',
    'PLaccount': 'Польша',
    'DEaccount': 'Германия',
    'USaccount': 'США',
    'UAaccount': 'Украина',
    'PREMIUMaccount': 'Премиум',
    'CUSTOMaccount': 'Кастомные',
    'KZaccount': 'Казахстан',
    'TWaccount': 'Тайвань',
    'AEaccount': 'ОАЭ',
    'ARaccount': 'Аргентина',
    'BYaccount': 'Беларусь',
    'CAaccount': 'Канада',
    'CHaccount': 'Швейцария',
    'CNaccount': 'Китай'
}
ALLOWED_CHANNELS = ['RU', 'PL', 'DE', 'US', 'UA', 'PREMIUM']
# ==============================================

spam_tasks: Dict[str, asyncio.Task] = {}
spam_status_messages: Dict[str, Tuple[int, int]] = {}

DIALOG_STATE = "spam_state"
STATE_AWAITING_SOURCE = 1
STATE_AWAITING_COUNT_MODE = 2
STATE_AWAITING_COMMON_COUNT = 3
STATE_AWAITING_INDIVIDUAL_COUNT = 4
STATE_AWAITING_UNIQUE_MODE = 5
STATE_AWAITING_SPEED = 6
STATE_AWAITING_CUSTOM_SPEED = 7
STATE_AWAITING_CYCLES = 8
STATE_AWAITING_PAUSE = 9
STATE_AWAITING_MSG_TYPE = 10
STATE_AWAITING_COMMON_MSG = 11

def generate_random_token(length=5) -> str:
    alphabet = string.ascii_letters
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def load_accounts_from_file(filename: str) -> Optional[List[Dict]]:
    filepath = os.path.join(SPAM_DATA_DIR, filename)
    if not os.path.exists(filepath):
        return None
    accounts = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'playerID' in row:
                    accounts.append({
                        'playerID': row['playerID'],
                        'token': generate_random_token(),
                        'source': filename.replace('.csv', '')
                    })
        return accounts
    except Exception:
        return []

def get_available_sources() -> Dict[str, List[Dict]]:
    if not os.path.exists(SPAM_DATA_DIR):
        os.makedirs(SPAM_DATA_DIR, exist_ok=True)
    sources = {}
    for fname in os.listdir(SPAM_DATA_DIR):
        if fname.endswith('.csv'):
            name = fname[:-4]
            accounts = load_accounts_from_file(fname)
            if accounts:
                sources[name] = accounts
    return sources

async def send_message_async(session: aiohttp.ClientSession, account: Dict, channel: str, message: str) -> bool:
    url = "https://api.efezgames.com/v1/social/sendChat"
    params = {
        "playerID": account['playerID'],
        "token": account['token'],
        "message": message,
        "channel": channel
    }
    try:
        async with session.get(url, params=params, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

async def spam_worker(
    channel: str,
    source_name: str,
    accounts: List[Dict],
    instant_messages: int,
    unique_mode: bool,
    random_mode: bool,
    messages_per_second: int,
    max_cycles: int,
    pause_between: int,
    message_text: str,
    chat_id: int,
    bot,
    status_message_id: int
):
    try:
        used_indices: Set[int] = set()
        cycle_count = 0
        total_accounts = len(accounts)
        fixed_accounts = accounts[:instant_messages] if not unique_mode and not random_mode else None

        semaphore = asyncio.Semaphore(MAX_WORKERS)
        delay_between = 1.0 / messages_per_second if messages_per_second > 0 else 0

        async with aiohttp.ClientSession() as session:
            while True:
                if max_cycles > 0 and cycle_count >= max_cycles:
                    break

                cycle_count += 1
                start_time = time.time()

                if unique_mode:
                    if len(used_indices) >= total_accounts:
                        used_indices.clear()
                    available = [i for i in range(total_accounts) if i not in used_indices]
                    if len(available) < instant_messages:
                        needed = instant_messages - len(available)
                        chosen = available.copy()
                        additional = random.sample(list(used_indices), needed)
                        chosen.extend(additional)
                    else:
                        chosen = random.sample(available, instant_messages)
                    used_indices.update(chosen)
                    cycle_accounts = [accounts[i] for i in chosen]
                    random.shuffle(cycle_accounts)
                elif random_mode:
                    cycle_accounts = random.sample(accounts, min(instant_messages, total_accounts))
                    random.shuffle(cycle_accounts)
                else:
                    cycle_accounts = fixed_accounts

                total = len(cycle_accounts)
                sent = 0
                tasks = []
                for acc in cycle_accounts:
                    async with semaphore:
                        task = asyncio.create_task(
                            send_message_async(session, acc, channel, message_text)
                        )
                        tasks.append(task)
                    if delay_between > 0:
                        await asyncio.sleep(delay_between)

                results = await asyncio.gather(*tasks, return_exceptions=True)
                sent = sum(1 for r in results if r is True)

                elapsed = time.time() - start_time
                progress_percent = (cycle_count / max_cycles * 100) if max_cycles > 0 else 0
                progress_bar = "█" * int(progress_percent / 10) + "░" * (10 - int(progress_percent / 10))
                status_text = (
                    f"📢 **Спам в канал {channel}**\n"
                    f"Цикл: {cycle_count}" + (f"/{max_cycles}" if max_cycles > 0 else "") + "\n"
                    f"Отправлено в цикле: {sent}/{total}\n"
                    f"Прогресс: {progress_bar} {progress_percent:.1f}%\n"
                    f"Скорость: {total/elapsed:.1f} сообщ/сек"
                )
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_message_id,
                        text=status_text,
                        parse_mode="Markdown"
                    )
                except:
                    pass

                if pause_between > 0 and (max_cycles == 0 or cycle_count < max_cycles):
                    await asyncio.sleep(pause_between)

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=f"✅ **Спам в канал {channel} завершён.**\nВыполнено циклов: {cycle_count}",
                parse_mode="Markdown"
            )
        except:
            pass

    except asyncio.CancelledError:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=f"🛑 **Спам в канал {channel} остановлен.**",
                parse_mode="Markdown"
            )
        except:
            pass
        raise
    finally:
        if channel in spam_tasks:
            del spam_tasks[channel]
        if channel in spam_status_messages:
            del spam_status_messages[channel]

async def cmd_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_id = update.effective_chat.id

    if not args:
        help_text = (
            "💥 **Спам-бот для Efez**\n\n"
            "Файлы с аккаунтами должны лежать в папке `spam_data/` в формате: `RUaccount.csv`, `USaccount.csv` и т.д.\n\n"
            "**Доступные каналы:** RU, PL, DE, US, UA, PREMIUM\n\n"
            "**Команды:**\n"
            "• `/spam start КАНАЛ` – начать настройку спама\n"
            "• `/spam stop КАНАЛ` – остановить спам в канале\n"
            "• `/spam status` – показать статус всех каналов\n\n"
            "**Текущий статус:**\n"
        )
        for ch in ALLOWED_CHANNELS:
            if ch in spam_tasks and not spam_tasks[ch].done():
                help_text += f"• {ch} ❌ (спам идёт)\n"
            else:
                help_text += f"• {ch} ✅\n"
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return

    if args[0].lower() == "status":
        text = "**Статус каналов:**\n"
        for ch in ALLOWED_CHANNELS:
            if ch in spam_tasks and not spam_tasks[ch].done():
                text += f"• {ch} ❌ спам активен\n"
            else:
                text += f"• {ch} ✅ свободен\n"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    if args[0].lower() == "stop" and len(args) >= 2:
        channel = args[1].upper()
        if channel not in ALLOWED_CHANNELS:
            await update.message.reply_text(f"❌ Неверный канал.")
            return
        if stop_spam_task(channel):
            await update.message.reply_text(f"✅ Спам в канал {channel} остановлен.")
        else:
            await update.message.reply_text(f"❌ Спам в канал {channel} не запущен.")
        return

    if args[0].lower() == "start" and len(args) >= 2:
        channel = args[1].upper()
        if channel not in ALLOWED_CHANNELS:
            await update.message.reply_text(f"❌ Неверный канал.")
            return

        if channel in spam_tasks and not spam_tasks[channel].done():
            await update.message.reply_text(f"❌ Спам в канал {channel} уже запущен.")
            return

        context.user_data['spam_channel'] = channel
        context.user_data[DIALOG_STATE] = STATE_AWAITING_SOURCE

        sources = get_available_sources()
        if not sources:
            await update.message.reply_text("❌ Нет файлов с аккаунтами в папке spam_data/.")
            context.user_data.clear()
            return

        context.user_data['spam_sources'] = sources
        source_list = list(sources.keys())
        source_list.sort()
        text = "📁 **Выберите источник аккаунтов:**\n\n"
        for idx, src in enumerate(source_list, 1):
            count = len(sources[src])
            desc = SOURCE_DESCRIPTIONS.get(src, src)
            text += f"{idx}. {src} [{count}] – {desc}\n"
        text += "\nВведите номер или имя источника:"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    await update.message.reply_text("Неверная команда. Используй /spam для справки.")

async def handle_spam_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get(DIALOG_STATE)
    if not state:
        return False

    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    channel = context.user_data.get('spam_channel')

    if state == STATE_AWAITING_SOURCE:
        sources = context.user_data.get('spam_sources', {})
        source_list = list(sources.keys())
        source_list.sort()
        selected_source = None

        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(source_list):
                selected_source = source_list[idx]
        else:
            if text in sources:
                selected_source = text

        if not selected_source:
            await update.message.reply_text("❌ Неверный выбор. Попробуйте ещё раз.")
            return True

        context.user_data['spam_source'] = selected_source
        context.user_data['spam_accounts'] = sources[selected_source]
        context.user_data[DIALOG_STATE] = STATE_AWAITING_COUNT_MODE

        await update.message.reply_text(
            f"✅ Выбран источник: {selected_source} ({len(sources[selected_source])} аккаунтов)\n\n"
            "**Настройка количества сообщений за цикл:**\n"
            "1 – Одинаковое количество (введи число)\n"
            "2 – Максимум (использовать все аккаунты)\n"
            "3 – Индивидуально (ввести число вручную)\n\n"
            "Введи номер режима (1/2/3):"
        )
        return True

    if state == STATE_AWAITING_COUNT_MODE:
        if text not in ('1', '2', '3'):
            await update.message.reply_text("❌ Введите 1, 2 или 3.")
            return True

        accounts = context.user_data['spam_accounts']
        total_accounts = len(accounts)

        if text == '2':
            context.user_data['spam_instant_messages'] = total_accounts
            context.user_data[DIALOG_STATE] = STATE_AWAITING_UNIQUE_MODE
            await update.message.reply_text(
                f"✅ Будет использовано максимум аккаунтов: {total_accounts}\n\n"
                "**Режим уникальности аккаунтов:**\n"
                "1 – Каждый цикл новые аккаунты\n"
                "2 – Случайные аккаунты каждый цикл\n"
                "3 – Фиксированные аккаунты (один набор)\n\n"
                "Введи номер режима (1/2/3):"
            )
            return True

        if text == '3':
            context.user_data[DIALOG_STATE] = STATE_AWAITING_INDIVIDUAL_COUNT
            await update.message.reply_text(
                f"Введите количество сообщений за цикл (от 1 до {total_accounts}):"
            )
            return True

        if text == '1':
            context.user_data[DIALOG_STATE] = STATE_AWAITING_COMMON_COUNT
            await update.message.reply_text(
                f"Введите количество сообщений за цикл (от 1 до {total_accounts}):"
            )
            return True

    if state == STATE_AWAITING_COMMON_COUNT or state == STATE_AWAITING_INDIVIDUAL_COUNT:
        try:
            count = int(text)
            accounts = context.user_data['spam_accounts']
            if 1 <= count <= len(accounts):
                context.user_data['spam_instant_messages'] = count
                context.user_data[DIALOG_STATE] = STATE_AWAITING_UNIQUE_MODE
                await update.message.reply_text(
                    f"✅ Установлено: {count} сообщений за цикл.\n\n"
                    "**Режим уникальности аккаунтов:**\n"
                    "1 – Каждый цикл новые аккаунты\n"
                    "2 – Случайные аккаунты каждый цикл\n"
                    "3 – Фиксированные аккаунты (один набор)\n\n"
                    "Введи номер режима (1/2/3):"
                )
            else:
                await update.message.reply_text(f"❌ Число должно быть от 1 до {len(accounts)}.")
        except ValueError:
            await update.message.reply_text("❌ Введите целое число.")
        return True

    if state == STATE_AWAITING_UNIQUE_MODE:
        if text not in ('1', '2', '3'):
            await update.message.reply_text("❌ Введите 1, 2 или 3.")
            return True

        if text == '1':
            context.user_data['spam_unique'] = True
            context.user_data['spam_random'] = False
        elif text == '2':
            context.user_data['spam_unique'] = False
            context.user_data['spam_random'] = True
        else:
            context.user_data['spam_unique'] = False
            context.user_data['spam_random'] = False

        context.user_data[DIALOG_STATE] = STATE_AWAITING_SPEED

        speed_menu = (
            "**Настройка скорости отправки:**\n"
            "1 – Мгновенно (без ограничений)\n"
            "2 – Очень быстро (100/сек)\n"
            "3 – Быстро (50/сек)\n"
            "4 – Средне (20/сек)\n"
            "5 – Медленно (5/сек)\n"
            "6 – Кастомная скорость\n\n"
            "Введи номер режима (1-6):"
        )
        await update.message.reply_text(speed_menu, parse_mode="Markdown")
        return True

    if state == STATE_AWAITING_SPEED:
        if text not in ('1','2','3','4','5','6'):
            await update.message.reply_text("❌ Введите 1-6.")
            return True

        speeds = {'1':0, '2':100, '3':50, '4':20, '5':5}
        if text in speeds:
            context.user_data['spam_mps'] = speeds[text]
            context.user_data[DIALOG_STATE] = STATE_AWAITING_CYCLES
            speed_text = "мгновенно" if text=='1' else f"{speeds[text]}/сек"
            await update.message.reply_text(f"✅ Скорость: {speed_text}\n\n**Сколько циклов выполнить?** (0 – бесконечно):")
        else:
            context.user_data[DIALOG_STATE] = STATE_AWAITING_CUSTOM_SPEED
            await update.message.reply_text("Введите количество сообщений в секунду (1-1000):")
        return True

    if state == STATE_AWAITING_CUSTOM_SPEED:
        try:
            mps = int(text)
            if 1 <= mps <= 1000:
                context.user_data['spam_mps'] = mps
                context.user_data[DIALOG_STATE] = STATE_AWAITING_CYCLES
                await update.message.reply_text(f"✅ Скорость: {mps}/сек.\n\n**Сколько циклов выполнить?** (0 – бесконечно):")
            else:
                await update.message.reply_text("❌ Введите число от 1 до 1000.")
        except ValueError:
            await update.message.reply_text("❌ Введите целое число.")
        return True

    if state == STATE_AWAITING_CYCLES:
        try:
            cycles = int(text)
            if cycles >= 0:
                context.user_data['spam_cycles'] = cycles
                context.user_data[DIALOG_STATE] = STATE_AWAITING_PAUSE
                await update.message.reply_text(
                    f"✅ Циклов: {'бесконечно' if cycles==0 else cycles}\n\n"
                    "**Пауза между циклами (секунд):** (0 – без паузы)"
                )
            else:
                await update.message.reply_text("❌ Введите 0 или положительное число.")
        except ValueError:
            await update.message.reply_text("❌ Введите целое число.")
        return True

    if state == STATE_AWAITING_PAUSE:
        try:
            pause = int(text)
            if pause >= 0:
                context.user_data['spam_pause'] = pause
                context.user_data[DIALOG_STATE] = STATE_AWAITING_MSG_TYPE
                await update.message.reply_text(
                    f"✅ Пауза: {pause} сек.\n\n"
                    "**Настройка сообщения:**\n"
                    "1 – Одно сообщение\n"
                    "2 – Стандартное 'Raid chat by @xuwyx'\n"
                    "Введи 1 или 2:"
                )
            else:
                await update.message.reply_text("❌ Введите 0 или положительное число.")
        except ValueError:
            await update.message.reply_text("❌ Введите целое число.")
        return True

    if state == STATE_AWAITING_MSG_TYPE:
        if text == '1':
            context.user_data[DIALOG_STATE] = STATE_AWAITING_COMMON_MSG
            await update.message.reply_text("Введите текст сообщения:")
        elif text == '2':
            context.user_data['spam_message'] = "Raid chat by @xuwyx"
            await finalize_spam_setup(update, context)
        else:
            await update.message.reply_text("❌ Введите 1 или 2.")
        return True

    if state == STATE_AWAITING_COMMON_MSG:
        msg = text if text else "Raid chat by @xuwyx"
        context.user_data['spam_message'] = msg
        await finalize_spam_setup(update, context)
        return True

    return False

async def finalize_spam_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    channel = context.user_data['spam_channel']
    source = context.user_data['spam_source']
    accounts = context.user_data['spam_accounts']
    instant = context.user_data['spam_instant_messages']
    unique = context.user_data.get('spam_unique', False)
    random_mode = context.user_data.get('spam_random', False)
    mps = context.user_data['spam_mps']
    cycles = context.user_data['spam_cycles']
    pause = context.user_data['spam_pause']
    message = context.user_data['spam_message']

    status_msg = await update.message.reply_text(
        f"🚀 **Запуск спама в канал {channel}**\n"
        f"Источник: {source} ({len(accounts)} акк.)\n"
        f"Сообщений за цикл: {instant}\n"
        f"Режим: {'уникальные' if unique else 'случайные' if random_mode else 'фиксированные'}\n"
        f"Скорость: {'мгновенно' if mps==0 else f'{mps}/сек'}\n"
        f"Циклов: {'бесконечно' if cycles==0 else cycles}\n"
        f"Пауза: {pause} сек.\n\n"
        f"Подготовка...",
        parse_mode="Markdown"
    )

    task = asyncio.create_task(
        spam_worker(
            channel=channel,
            source_name=source,
            accounts=accounts,
            instant_messages=instant,
            unique_mode=unique,
            random_mode=random_mode,
            messages_per_second=mps,
            max_cycles=cycles,
            pause_between=pause,
            message_text=message,
            chat_id=chat_id,
            bot=context.bot,
            status_message_id=status_msg.message_id
        )
    )

    spam_tasks[channel] = task
    spam_status_messages[channel] = (chat_id, status_msg.message_id)

    context.user_data.clear()

async def handle_spam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_spam(update, context)

async def handle_spam_dialog_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return await handle_spam_dialog(update, context)

def stop_spam_task(channel: str) -> bool:
    if channel in spam_tasks and not spam_tasks[channel].done():
        spam_tasks[channel].cancel()
        return True
    return False

def get_active_spam_tasks() -> List[str]:
    return [ch for ch, task in spam_tasks.items() if not task.done()]
