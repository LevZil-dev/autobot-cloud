"""
Telegram Bot Service (aiogram 3.x).
Provides remote status monitoring, interactive approvals, screenshot requests, and email control panel.
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

# Safe imports for config, notifier and registry
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv()

try:
    import config
except ImportError:
    config = None

from tg_bot.process_registry import process_registry
from tg_bot.notifier import tg_notifier

# Safe import for PC automation tools (available on PC, absent in cloud)
try:
    import tools
except ImportError:
    tools = None

# Safe import for EmailMonitor
try:
    from cloud.email_monitor import EmailMonitor
except ImportError:
    try:
        from email_monitor import EmailMonitor
    except ImportError:
        EmailMonitor = None

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_email_monitor_instance = None


def get_email_monitor():
    global _email_monitor_instance
    if _email_monitor_instance is None and EmailMonitor is not None:
        try:
            _email_monitor_instance = EmailMonitor()
        except Exception as e:
            print(f"[Bot Service] Warning: EmailMonitor init error: {e}")
    return _email_monitor_instance


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Main persistent navigation keyboard for Telegram."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📬 Управление почтой"), KeyboardButton(text="📊 Статус процессов")],
            [KeyboardButton(text="📸 Снимок экрана"), KeyboardButton(text="🧹 Очистить кэш")],
            [KeyboardButton(text="⚡ Проверка связи")],
        ],
        resize_keyboard=True,
    )


def get_email_inline_keyboard(dry_run: bool = True) -> InlineKeyboardMarkup:
    """Builds inline menu for email management."""
    mode_text = "🛡️ Симуляция (Тест)" if dry_run else "⚡ Удаление в корзину"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Топ отправителей", callback_data="mail:senders"),
                InlineKeyboardButton(text="📩 Свежие письма", callback_data="mail:recent"),
            ],
            [
                InlineKeyboardButton(text="🏷️ Умная сортировка", callback_data="mail:sort"),
                InlineKeyboardButton(text="🧹 Очистить спам сейчас", callback_data="mail:clean"),
            ],
            [
                InlineKeyboardButton(text=f"⚙️ {mode_text}", callback_data="mail:toggle_mode"),
                InlineKeyboardButton(text="📊 Статистика ящика", callback_data="mail:stats"),
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="mail:refresh"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="mail:close"),
            ],
        ]
    )


def format_email_menu_text(stats: dict) -> str:
    user = stats.get("user") or "Не настроен"
    total = stats.get("total", 0)
    unread = stats.get("unread", 0)
    dry_run = stats.get("dry_run", True)
    mode_badge = "🛡️ *Симуляция (Dry Run)* — письма не удаляются" if dry_run else "⚡ *Боевой режим* — спам отправляется в Корзину"

    return (
        f"📬 *Панель управления почтой*\n\n"
        f"• **Ящик:** `{user}`\n"
        f"• **Непрочитанных писем:** `{unread:,}`\n"
        f"• **Всего во Входящих:** `{total:,}`\n"
        f"• **Режим очистки:** {mode_badge}\n"
        f"• **Классификатор:** `Gemini 3.8 Flash`\n\n"
        f"_Выберите действие в быстрой панели ниже:_"
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
            f"Вам доступны быстрые функции:\n"
            f"• 📬 *Управление почтой* — аналитика, топ отправителей, умная сортировка и очистка спама.\n"
            f"• 📊 *Статус процессов* — мониторинг всех фоновых задач и облачных воркеров (`/status`).\n"
            f"• 📸 *Снимок экрана* — удалённый скриншот вашего ПК (`/screenshot`).\n"
            f"• ⚡ *Проверка связи* — пинг и статус соединения."
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
        msg = await message.answer("📡 Проверка соединения...")
        latency = int((time.time() - t0) * 1000)
        await msg.edit_text(
            f"✅ *Соединение стабильно!*\n"
            f"• Время отклика: `{latency} ms`\n"
            f"• Платформа: AutoBot Cloud / Local\n"
            f"• Статус: Онлайн 24/7",
            parse_mode="Markdown",
        )

    @dp.message(Command("screenshot"))
    @dp.message(F.text == "📸 Снимок экрана")
    async def cmd_screenshot(message: types.Message):
        if tools is None:
            await message.answer("📸 Снимок экрана доступен на вашем локальном ПК, когда запущен клиент AutoBot.")
            return

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
        if tools is None:
            await message.answer("🧹 Очистка локального кэша доступна на локальном ПК.")
            return
        res = tools.cleaner.clean_temp_files(max_age_seconds=0)
        await message.answer(f"🧹 *Очистка завершена:*\n{res}", parse_mode="Markdown")

    # ==========================================
    # EMAIL MANAGEMENT FAST PANEL & COMMANDS
    # ==========================================
    @dp.message(Command("email"))
    @dp.message(F.text == "📬 Управление почтой")
    async def cmd_email_menu(message: types.Message):
        monitor = get_email_monitor()
        if not monitor:
            await message.answer("❌ Модуль управления почтой недоступен. Проверьте настройки .env.")
            return

        status_msg = await message.answer("🔄 Подключение к почтовому ящику...")
        try:
            stats = await asyncio.to_thread(monitor.get_mailbox_stats)
            text = format_email_menu_text(stats)
            kb = get_email_inline_keyboard(stats.get("dry_run", True))
            await status_msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка подключения к почте: {e}")

    @dp.callback_query(F.data.startswith("mail:"))
    async def handle_email_callback(query: types.CallbackQuery):
        action = query.data.split(":", 1)[1]
        monitor = get_email_monitor()
        if not monitor:
            await query.answer("Модуль почты не загружен", show_alert=True)
            return

        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🧹 Очистить спам", callback_data="mail:clean"),
                    InlineKeyboardButton(text="🔙 Назад к почте", callback_data="mail:refresh"),
                ]
            ]
        )

        if action in ("refresh", "menu"):
            await query.answer("Обновление данных...")
            try:
                stats = await asyncio.to_thread(monitor.get_mailbox_stats)
                text = format_email_menu_text(stats)
                kb = get_email_inline_keyboard(stats.get("dry_run", True))
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                await query.answer(f"Ошибка: {e}", show_alert=True)

        elif action == "senders":
            await query.answer("Анализ отправителей...")
            await query.message.edit_text("⏳ Анализирую недавние входящие сообщения...")
            try:
                senders = await asyncio.to_thread(monitor.get_top_senders, 8, 35)
                if not senders:
                    text = "👥 *Отправители:* Непрочитанных писем не найдено."
                else:
                    lines = ["👥 *Топ отправителей непрочитанных писем:*\n"]
                    for i, item in enumerate(senders, 1):
                        s_name = item["sender"]
                        count = item["count"]
                        lines.append(f"**{i}.** `{s_name}` — **{count}** писем")
                    lines.append("\n💡 _Совет: чтобы очистить спам от неизвестных отправителей, нажмите кнопку ниже._")
                    text = "\n".join(lines)
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка анализа отправителей: {e}", reply_markup=back_kb)

        elif action == "recent":
            await query.answer("Загрузка писем...")
            await query.message.edit_text("⏳ Получаю свежие заголовки писем...")
            try:
                recent = await asyncio.to_thread(monitor.get_recent_emails, 5)
                if not recent:
                    text = "📩 *Входящие:* Непрочитанных писем нет."
                else:
                    lines = ["📩 *Последние входящие письма:*\n"]
                    for i, r in enumerate(recent, 1):
                        lines.append(
                            f"**{i}.** 👤 `{r['sender']}`\n"
                            f"   📌 *Тема:* {r['subject']}\n"
                            f"   📅 _{r['date']}_\n"
                        )
                    text = "\n".join(lines)
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка получения писем: {e}", reply_markup=back_kb)

        elif action == "sort":
            await query.answer("Умная сортировка...")
            await query.message.edit_text("⏳ Классифицирую недавние письма по категориям...")
            try:
                categories = await asyncio.to_thread(monitor.smart_sort_unseen, 15)
                lines = ["🏷️ *Умная сортировка входящих писем:*\n"]

                imp = categories.get("IMPORTANT", [])
                news = categories.get("NEWSLETTER", [])
                soc = categories.get("SOCIAL", [])
                spm = categories.get("SPAM", [])

                lines.append(f"🟢 *Важные ({len(imp)}):*")
                for item in imp[:3]:
                    lines.append(f"• `{item['sender']}`: {item['subject']}")
                if not imp:
                    lines.append("• _(нет)_")

                lines.append(f"\n📢 *Рассылки и магазины ({len(news)}):*")
                for item in news[:3]:
                    lines.append(f"• `{item['sender']}`: {item['subject']}")
                if not news:
                    lines.append("• _(нет)_")

                lines.append(f"\n👥 *Соцсети и сервисы ({len(soc)}):*")
                for item in soc[:3]:
                    lines.append(f"• `{item['sender']}`: {item['subject']}")
                if not soc:
                    lines.append("• _(нет)_")

                lines.append(f"\n🚫 *Спам и подозрительные ({len(spm)}):*")
                for item in spm[:3]:
                    lines.append(f"• `{item['sender']}`: {item['subject']}")
                if not spm:
                    lines.append("• _(спама не обнаружено)_")

                text = "\n".join(lines)
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка сортировки: {e}", reply_markup=back_kb)

        elif action == "clean":
            await query.answer("Запуск очистки спама...")
            await query.message.edit_text("⏳ Сканирую свежие письма через Gemini AI...")
            try:
                stats = await asyncio.to_thread(monitor.process_unread_emails)
                checked = stats.get("checked", 0)
                spam_found = stats.get("spam", 0)
                important = stats.get("important", 0)
                total_left = stats.get("total_unseen", 0)
                mode_label = "🛡️ Симуляция (письма не тронуты)" if monitor.dry_run else "⚡ Спам перемещен в Корзину"

                text = (
                    f"🧹 *Результаты сканирования:*\n\n"
                    f"• Проверено писем в пачке: `{checked}`\n"
                    f"• Выявлено спама: `{spam_found}`\n"
                    f"• Оставлено во Входящих: `{important}`\n"
                    f"• Всего непрочитанных в ящике: `{total_left}`\n"
                    f"• Режим: {mode_label}\n"
                )
                if stats.get("actions"):
                    text += "\n*Действия:*\n" + "\n".join(stats["actions"][:4])
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка очистки: {e}", reply_markup=back_kb)

        elif action == "toggle_mode":
            new_mode = monitor.toggle_dry_run()
            status_text = "🛡️ Режим симуляции включен!" if new_mode else "⚡ Боевой режим включен (спам будет удаляться)!"
            await query.answer(status_text, show_alert=True)
            stats = await asyncio.to_thread(monitor.get_mailbox_stats)
            text = format_email_menu_text(stats)
            kb = get_email_inline_keyboard(new_mode)
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

        elif action == "stats":
            await query.answer("Загрузка статистики...")
            try:
                stats = await asyncio.to_thread(monitor.get_mailbox_stats)
                text = (
                    f"📊 *Детальная статистика ящика:*\n\n"
                    f"• **Адрес:** `{stats.get('user')}`\n"
                    f"• **Провайдер:** `Gmail (IMAP SSL)`\n"
                    f"• **Всего писем в INBOX:** `{stats.get('total'):,}`\n"
                    f"• **Непрочитанных писем:** `{stats.get('unread'):,}`\n"
                    f"• **AI-защита:** `{'Включена (Gemini 3.8 Flash)' if stats.get('ai_active') else 'Выключена'}`\n"
                    f"• **Режим удаления:** `{'Симуляция' if stats.get('dry_run') else 'Боевое удаление'}`\n"
                )
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка: {e}", reply_markup=back_kb)

        elif action == "close":
            await query.answer("Панель закрыта")
            await query.message.delete()

    # Inline Button Callback Handler (Confirmations / Approvals)
    @dp.callback_query(F.data.startswith("confirm:") | F.data.startswith("reject:"))
    async def handle_confirmation_callback(query: types.CallbackQuery):
        data = query.data
        approved = data.startswith("confirm:")
        action_id = data.split(":", 1)[1]

        result_text = tg_notifier.handle_decision(action_id, approved)
        await query.answer(result_text)

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
        if text.startswith("/") or text in ["📊 Статус процессов", "📸 Снимок экрана", "⚡ Проверка связи", "🧹 Очистить кэш", "📬 Управление почтой"]:
            return

        process_registry.update("autobot_local", status="RUNNING", details=f"Выполняет: '{text}'")
        status_msg = await message.answer(f"⚙️ Выполняю команду на ПК: *'{text}'*...", parse_mode="Markdown")

        try:
            text_lower = text.lower()
            if any(k in text_lower for k in ["калькулятор", "calc", "дискорд", "discord", "диспетчер", "taskmgr", "блокнот", "notepad"]):
                app_name = "calc" if "кальк" in text_lower or "calc" in text_lower else (
                    "discord" if "дискорд" in text_lower or "discord" in text_lower else (
                        "taskmgr" if "диспетчер" in text_lower or "taskmgr" in text_lower else "notepad"
                    )
                )
                if tools is not None:
                    res = tools.pc_control.open_application(app_name)
                    await status_msg.edit_text(f"✅ {res}")
                else:
                    await status_msg.edit_text("ℹ️ Команды запуска приложений на ПК доступны при локальной работе AutoBot.")
            else:
                try:
                    from bot_core import AutoBotEngine
                    engine = AutoBotEngine()
                    res = await engine.execute_task(text)
                    await status_msg.edit_text(f"🤖 *AutoBot:*\n{res}", parse_mode="Markdown")
                except Exception as ex:
                    await status_msg.edit_text(f"ℹ️ {ex}")

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
