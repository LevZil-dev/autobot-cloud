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
_sender_cache: dict[int, dict[int, dict]] = {}
_waiting_for_custom_delete: set[int] = set()
_custom_delete_cache: dict[int, str] = {}


def get_email_monitor():
    global _email_monitor_instance
    if _email_monitor_instance is not None:
        return _email_monitor_instance

    cls = EmailMonitor
    if cls is None:
        for mod_name in ["email_monitor", "cloud.email_monitor"]:
            try:
                mod = __import__(mod_name, fromlist=["EmailMonitor"])
                cls = getattr(mod, "EmailMonitor", None)
                if cls:
                    break
            except Exception:
                pass

    if cls is not None:
        try:
            _email_monitor_instance = cls()
            return _email_monitor_instance
        except Exception as e:
            print(f"[Bot Service] Warning: EmailMonitor init error: {e}")
            raise e
    return None


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


async def create_dispatcher(email_monitor=None) -> tuple[Bot, Dispatcher]:
    global _email_monitor_instance
    if email_monitor is not None:
        _email_monitor_instance = email_monitor
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
        status_msg = await message.answer("🔄 Подключение к почтовому ящику...")
        try:
            monitor = get_email_monitor()
            if not monitor:
                await status_msg.edit_text("❌ Модуль управления почтой не найден. Проверьте настройки EMAIL_USER / EMAIL_PASS.")
                return

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
            await query.answer("Отслеживание всех отправителей...")
            await query.message.edit_text("⏳ Анализирую входящие сообщения и активных отправителей...")
            try:
                user_id = query.from_user.id
                tracked = await asyncio.to_thread(monitor.get_tracked_senders, 50, 8)
                if not tracked:
                    text = "👥 *Отслеживание отправителей:* Непрочитанных писем не найдено."
                    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
                else:
                    _sender_cache[user_id] = {i: s for i, s in enumerate(tracked, 1)}
                    lines = [
                        "👥 *Отслеживание отправителей (Входящие)*\n"
                        "_(Рейтинг активности по недавним непрочитанным письмам)_\n"
                    ]
                    for i, item in enumerate(tracked, 1):
                        lines.append(f"**{i}.** 👤 **{item['name']}**\n   📧 `{item['email']}` — **{item['unread']}** непр.\n")
                    lines.append("👇 *Нажмите на отправителя, чтобы удалить все письма от него:*")
                    text = "\n".join(lines)

                    sender_buttons = []
                    row = []
                    for i, s in enumerate(tracked, 1):
                        s_label = f"🗑️ {i}. {s['name'][:10]}"
                        row.append(InlineKeyboardButton(text=s_label, callback_data=f"sndr:p:{i}"))
                        if len(row) == 2:
                            sender_buttons.append(row)
                            row = []
                    if row:
                        sender_buttons.append(row)

                    sender_buttons.append([
                        InlineKeyboardButton(text="🔍 Удалить по email/домену", callback_data="sndr:custom"),
                        InlineKeyboardButton(text="🔄 Обновить", callback_data="mail:senders"),
                    ])
                    sender_buttons.append([
                        InlineKeyboardButton(text="🔙 Назад к почте", callback_data="mail:refresh"),
                    ])
                    kb = InlineKeyboardMarkup(inline_keyboard=sender_buttons)
                    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
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

    @dp.callback_query(F.data.startswith("sndr:"))
    async def handle_sender_callbacks(query: types.CallbackQuery):
        user_id = query.from_user.id
        parts = query.data.split(":")
        sub = parts[1]
        monitor = get_email_monitor()
        if not monitor:
            await query.answer("Модуль почты недоступен", show_alert=True)
            return

        if sub == "p":
            idx = int(parts[2])
            user_cache = _sender_cache.get(user_id, {})
            sender = user_cache.get(idx)
            if not sender:
                await query.answer("Данные устарели, обновите список", show_alert=True)
                return

            await query.answer("Загрузка статистики отправителя...")
            email_addr = sender["email"]
            name = sender["name"]
            details = await asyncio.to_thread(monitor.get_sender_details, email_addr)

            total_count = details.get("total", 0)
            unread_count = details.get("unread", 0)

            text = (
                f"📧 *Управление письмами отправителя:*\n\n"
                f"• **Имя:** {name}\n"
                f"• **Email:** `{email_addr}`\n"
                f"• **Непрочитанных писем:** **{unread_count:,}**\n"
                f"• **Всего во Входящих:** **{total_count:,}**\n\n"
                f"👇 _Выберите вариант удаления:_"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text=f"🗑️ Только непрочитанные ({unread_count})", callback_data=f"sndr:ask:u:{idx}"),
                    ],
                    [
                        InlineKeyboardButton(text=f"🔥 ВСЕ письма во Входящих ({total_count})", callback_data=f"sndr:ask:a:{idx}"),
                    ],
                    [
                        InlineKeyboardButton(text="🔙 К списку отправителей", callback_data="mail:senders"),
                    ],
                ]
            )
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

        elif sub == "ask":
            del_type = parts[2]
            idx = int(parts[3])
            user_cache = _sender_cache.get(user_id, {})
            sender = user_cache.get(idx)
            if not sender:
                await query.answer("Данные устарели", show_alert=True)
                return

            type_desc = "непрочитанных" if del_type == "u" else "ВСЕХ"
            text = (
                f"⚠️ *Подтверждение удаления писем!*\n\n"
                f"Вы уверены, что хотите переместить в Корзину **{type_desc}** письма от:\n"
                f"📧 `{sender['email']}` ({sender['name']})?\n\n"
                f"💡 _Письма будут помещены в папку «Корзина» (хранятся 30 дней)._"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🔥 Да, переместить в Корзину", callback_data=f"sndr:do:{del_type}:{idx}"),
                    ],
                    [
                        InlineKeyboardButton(text="❌ Отмена", callback_data=f"sndr:p:{idx}"),
                    ],
                ]
            )
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

        elif sub == "do":
            del_type = parts[2]
            idx = int(parts[3])
            user_cache = _sender_cache.get(user_id, {})
            sender = user_cache.get(idx)
            if not sender:
                await query.answer("Данные устарели", show_alert=True)
                return

            only_unread = (del_type == "u")
            await query.answer("Удаление писем...")
            await query.message.edit_text(f"⏳ Перемещаю письма от `{sender['email']}` в Корзину...")

            res = await asyncio.to_thread(monitor.delete_all_from_sender, sender["email"], only_unread=only_unread, force=True)
            deleted_count = res.get("count", 0)

            text = (
                f"✅ *Очистка завершена!*\n\n"
                f"• **Перемещено в Корзину:** `{deleted_count}` писем\n"
                f"• **Отправитель:** `{sender['email']}`\n"
                f"• **Статус:** В Корзине 🗑️\n"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="👥 К списку отправителей", callback_data="mail:senders"),
                        InlineKeyboardButton(text="📬 Главное меню", callback_data="mail:refresh"),
                    ]
                ]
            )
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

        elif sub == "custom":
            _waiting_for_custom_delete.add(user_id)
            await query.answer()
            await query.message.answer(
                "✏️ *Удаление писем конкретного отправителя:*\n\n"
                "Отправьте в чат email или домен (например: `news@news.ozon.ru` или `aliexpress.com`), "
                "чтобы найти и удалить все письма от него.",
                parse_mode="Markdown"
            )

        elif sub == "c_do":
            del_type = parts[2]
            target = _custom_delete_cache.get(user_id)
            if not target:
                await query.answer("Цель не найдена", show_alert=True)
                return

            only_unread = (del_type == "u")
            await query.answer("Удаление...")
            await query.message.edit_text(f"⏳ Перемещаю письма от `{target}` в Корзину...")
            res = await asyncio.to_thread(monitor.delete_all_from_sender, target, only_unread=only_unread, force=True)
            deleted = res.get("count", 0)

            text = (
                f"✅ *Очистка завершена!*\n\n"
                f"• **Удалено писем:** `{deleted}`\n"
                f"• **Целевой адрес:** `{target}`\n"
                f"• **Статус:** Перемещены в Корзину 🗑️"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="👥 К списку отправителей", callback_data="mail:senders"),
                        InlineKeyboardButton(text="📬 Меню почты", callback_data="mail:refresh"),
                    ]
                ]
            )
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)

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
        user_id = message.from_user.id

        if user_id in _waiting_for_custom_delete:
            _waiting_for_custom_delete.discard(user_id)
            target = text.strip().lower()
            status_msg = await message.answer(f"🔍 Поиск писем от '{target}'...")
            monitor = get_email_monitor()
            if not monitor:
                await status_msg.edit_text("❌ Модуль управления почтой недоступен.")
                return

            details = await asyncio.to_thread(monitor.get_sender_details, target)
            total = details.get("total", 0)
            unread = details.get("unread", 0)
            if total == 0:
                await status_msg.edit_text(f"ℹ️ Во Входящих не найдено писем от `{target}`.")
                return

            _custom_delete_cache[user_id] = target
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text=f"🗑️ Непрочитанные ({unread})", callback_data="sndr:c_do:u"),
                        InlineKeyboardButton(text=f"🔥 ВСЕ письма ({total})", callback_data="sndr:c_do:a"),
                    ],
                    [
                        InlineKeyboardButton(text="❌ Отмена", callback_data="mail:senders"),
                    ],
                ]
            )
            await status_msg.edit_text(
                f"📧 *Найдено писем от:* `{target}`\n\n"
                f"• **Непрочитанных:** **{unread}**\n"
                f"• **Всего во Входящих:** **{total}**\n\n"
                f"👇 _Выберите, какие письма переместить в Корзину:_",
                parse_mode="Markdown",
                reply_markup=kb,
            )
            return

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
