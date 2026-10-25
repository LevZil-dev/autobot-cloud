"""
Telegram Bot Service (aiogram 3.x).
Provides remote status monitoring, interactive approvals, screenshot requests, and remote commands.
"""
import os
import sys
import time
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

import config
from tg_bot.process_registry import process_registry
from tg_bot.notifier import tg_notifier
import tools

# Load credentials
load_dotenv(config.BASE_DIR / ".env")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Main persistent navigation keyboard for Telegram."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус процессов"), KeyboardButton(text="📸 Снимок экрана")],
            [KeyboardButton(text="⚡ Проверка связи"), KeyboardButton(text="🧹 Очистить кэш")],
        ],
        resize_keyboard=True,
    )


async def create_dispatcher() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def cmd_start(message: types.Message):
        user_id = message.from_user.id
        tg_notifier.set_admin_chat_id(user_id)
        process_registry.update("autobot_local", status="ONLINE", details="Telegram Hub подключен")

        welcome_text = (
            f"👋 *Привет, {message.from_user.first_name}!*\n\n"
            f"🤖 *AutoBot Control Hub* успешно подключен к вашему Telegram-аккаунту.\n\n"
            f"🔑 Ваш Chat ID: `{user_id}` сохранён как администратор.\n\n"
            f"Теперь вы можете:\n"
            f"• Отслеживать все автономные процессы и облачных ботов (`/status`).\n"
            f"• Получать мгновенные алерты и кнопки подтверждения действий.\n"
            f"• Получать удалённый снимок экрана вашего ПК (`/screenshot`).\n"
            f"• Отправлять прямые команды на ваш ПК прямо в этот чат!"
        )
        await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

    @dp.message(Command("status"))
    @dp.message(F.text == "📊 Статус процессов")
    async def cmd_status(message: types.Message):
        status_md = process_registry.get_formatted_status()
        await message.answer(status_md, parse_mode="Markdown")

    @dp.message(Command("ping"))
    @dp.message(F.text == "⚡ Проверка связи")
    async def cmd_ping(message: types.Message):
        t0 = time.time()
        msg = await message.answer("📡 Проверка соединения с ПК...")
        latency = int((time.time() - t0) * 1000)
        await msg.edit_text(
            f"✅ *Соединение стабильно!*\n"
            f"• Время отклика: `{latency} ms`\n"
            f"• Хост: Windows (AutoBot v2)\n"
            f"• Статус: Онлайн",
            parse_mode="Markdown",
        )

    @dp.message(Command("screenshot"))
    @dp.message(F.text == "📸 Снимок экрана")
    async def cmd_screenshot(message: types.Message):
        status_msg = await message.answer("📸 Захватываю экран ПК...")
        try:
            res = await asyncio.to_thread(tools.screen_vision.take_screenshot, "full")
            if "Screenshot saved to " in res:
                path_part = res.split("Screenshot saved to ")[-1].split(" (")[0].strip()
                if os.path.exists(path_part):
                    photo_file = types.FSInputFile(path_part)
                    await message.answer_photo(
                        photo=photo_file,
                        caption=f"🖥 Снимок экрана ПК ({time.strftime('%H:%M:%S')})",
                    )
                    await status_msg.delete()
                    try:
                        os.remove(path_part)
                    except Exception:
                        pass
                    return
            await status_msg.edit_text(f"❌ {res}")
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка: {e}")

    @dp.message(F.text == "🧹 Очистить кэш")
    async def cmd_cleanup(message: types.Message):
        res = tools.cleaner.clean_temp_files(max_age_seconds=0)
        await message.answer(f"🧹 *Очистка завершена:*\n{res}", parse_mode="Markdown")

    # Inline Button Callback Handler (Confirmations / Approvals)
    @dp.callback_query(F.data.startswith("confirm:") | F.data.startswith("reject:"))
    async def handle_confirmation_callback(query: types.CallbackQuery):
        data = query.data
        approved = data.startswith("confirm:")
        action_id = data.split(":", 1)[1]

        result_text = tg_notifier.handle_decision(action_id, approved)
        await query.answer(result_text)

        # Update the original message text to reflect the decision
        current_text = query.message.text or ""
        decision_badge = "✅ РАЗРЕШЕНО" if approved else "❌ ОТКЛОНЕНО"
        await query.message.edit_text(
            f"{current_text}\n\n*Решение пользователя:* {decision_badge}",
            parse_mode="Markdown",
            reply_markup=None,
        )

    # Free-form commands to AutoBot
    @dp.message(F.text)
    async def handle_remote_command(message: types.Message):
        text = message.text.strip()
        if text.startswith("/"):
            return

        process_registry.update("autobot_local", status="RUNNING", details=f"Выполняет: '{text}'")
        status_msg = await message.answer(f"⚙️ Выполняю команду на ПК: *'{text}'*...", parse_mode="Markdown")

        try:
            # Check for direct simple application launches first for instant speed
            text_lower = text.lower()
            if any(k in text_lower for k in ["калькулятор", "calc", "дискорд", "discord", "диспетчер", "taskmgr", "блокнот", "notepad"]):
                app_name = "calc" if "кальк" in text_lower or "calc" in text_lower else (
                    "discord" if "дискорд" in text_lower or "discord" in text_lower else (
                        "taskmgr" if "диспетчер" in text_lower or "taskmgr" in text_lower else "notepad"
                    )
                )
                res = tools.pc_control.open_application(app_name)
                await status_msg.edit_text(f"✅ {res}")
            else:
                # Dispatch to AutoBot Core Engine
                from bot_core import AutoBotEngine
                engine = AutoBotEngine()
                res = await engine.execute_task(text)
                await status_msg.edit_text(f"🤖 *AutoBot:*\n{res}", parse_mode="Markdown")

            process_registry.update("autobot_local", status="ONLINE", details="Команда выполнена успешно")
        except Exception as e:
            process_registry.update("autobot_local", status="ERROR", details=str(e))
            await status_msg.edit_text(f"❌ Ошибка выполнения: {e}")

    return bot, dp


async def run_bot():
    """Runs polling loop for the Telegram Bot."""
    if not BOT_TOKEN:
        print("❌ ОШИБКА: TELEGRAM_BOT_TOKEN не задан в .env!")
        return

    bot, dp = await create_dispatcher()
    print("\n" + "=" * 55)
    print("🚀 Telegram Bot Hub запущен!")
    print(f"🤖 Бот: @testformeandus_bot")
    print("Откройте Telegram и отправьте /start вашему боту.")
    print("=" * 55 + "\n")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_bot())
