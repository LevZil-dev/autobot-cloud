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


# Safe imports for Broker modules
try:
    from cloud.broker import BrokerSecurity, TinkoffClient, NewsAnalyzer, RecommendationEngine
except ImportError:
    try:
        from broker import BrokerSecurity, TinkoffClient, NewsAnalyzer, RecommendationEngine
    except ImportError:
        BrokerSecurity = None
        TinkoffClient = None
        NewsAnalyzer = None
        RecommendationEngine = None

_tinkoff_client_instance = None
_news_analyzer_instance = None
_recommendation_engine_instance = None
_waiting_for_ticker_analysis: set[int] = set()
_waiting_for_broker_token: set[int] = set()


def get_tinkoff_client():
    global _tinkoff_client_instance
    if _tinkoff_client_instance is None and TinkoffClient is not None:
        _tinkoff_client_instance = TinkoffClient()
    return _tinkoff_client_instance


def get_news_analyzer():
    global _news_analyzer_instance
    if _news_analyzer_instance is None and NewsAnalyzer is not None:
        _news_analyzer_instance = NewsAnalyzer()
    return _news_analyzer_instance


def get_recommendation_engine():
    global _recommendation_engine_instance
    if _recommendation_engine_instance is None and RecommendationEngine is not None:
        _recommendation_engine_instance = RecommendationEngine(news_analyzer=get_news_analyzer())
    return _recommendation_engine_instance


def get_broker_inline_keyboard(is_configured: bool = False) -> InlineKeyboardMarkup:
    """Builds inline menu for Broker & AI Investment Assistant."""
    token_btn_text = "🔑 Изменить токен" if is_configured else "🔑 Подключить Т-Инвестиции"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💼 Мой портфель", callback_data="brk:portfolio"),
                InlineKeyboardButton(text="🎯 Топ предложений (Идеи)", callback_data="brk:ideas"),
            ],
            [
                InlineKeyboardButton(text="📰 AI-Пульс новостей", callback_data="brk:news"),
                InlineKeyboardButton(text="🔍 Анализ тикера (акции)", callback_data="brk:search"),
            ],
            [
                InlineKeyboardButton(text=token_btn_text, callback_data="brk:token"),
                InlineKeyboardButton(text="🛡️ Безопасность и API", callback_data="brk:security"),
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="brk:refresh"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="brk:close"),
            ],
        ]
    )


def format_broker_menu_text(portfolio_data: dict, is_configured: bool) -> str:
    sec_status = "🔒 *Только чтение (Read-Only)* — сделки заблокированы"
    mode_badge = "🟢 *Т-Инвестиции API подключен*" if is_configured else "🟡 *Демо-режим (Голубые фишки РФ)*"
    total = portfolio_data.get("total_portfolio_rub", 0.0)
    expected_yield = portfolio_data.get("expected_yield_rub", 0.0)
    yield_sign = "+" if expected_yield >= 0 else ""
    positions_count = portfolio_data.get("positions_count", 0)

    return (
        f"📈 *AI-Брокер & Инвестиционный ассистент*\n\n"
        f"• **Статус:** {mode_badge}\n"
        f"• **Режим безопасности:** {sec_status}\n"
        f"• **AI-аналитик:** `Gemini 3.8 Flash (MOEX & T-Invest)`\n"
        f"• **Оценка портфеля:** `{total:,.2f} ₽`\n"
        f"• **Доходность:** `{yield_sign}{expected_yield:,.2f} ₽` ({positions_count} позиций)\n\n"
        f"_Выберите раздел в быстрой панели ниже:_"
    )


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Main persistent navigation keyboard for Telegram."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📈 Брокер"), KeyboardButton(text="📬 Управление почтой")],
            [KeyboardButton(text="📊 Статус процессов"), KeyboardButton(text="📸 Снимок экрана")],
            [KeyboardButton(text="⚡ Проверка связи"), KeyboardButton(text="🧹 Очистить кэш")],
        ],
        resize_keyboard=True,
    )


def get_email_inline_keyboard(dry_run: bool = True) -> InlineKeyboardMarkup:
    """Builds inline menu for email management."""
    mode_text = "🛡️ Симуляция (Тест)" if dry_run else "⚡ Удаление в корзину"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Отчет мониторинга", callback_data="mail:report"),
                InlineKeyboardButton(text="👥 Все отправители (Каталог)", callback_data="mail:all_senders:1"),
            ],
            [
                InlineKeyboardButton(text="📩 Свежие письма", callback_data="mail:recent"),
                InlineKeyboardButton(text="🏷️ Умная сортировка", callback_data="mail:sort"),
            ],
            [
                InlineKeyboardButton(text="🧹 Очистить спам сейчас", callback_data="mail:clean"),
                InlineKeyboardButton(text="📊 Статистика ящика", callback_data="mail:stats"),
            ],
            [
                InlineKeyboardButton(text=f"⚙️ {mode_text}", callback_data="mail:toggle_mode"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="mail:refresh"),
            ],
            [
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


async def show_email_monitoring_report(
    target: types.Message | types.CallbackQuery,
    monitor,
    run_fresh_scan: bool = False,
):
    """
    Отдельная функция вызова и отображения отчёта мониторинга почты.
    Используется внутри команды /email, из inline-меню, а также при прямой команде /email report.
    """
    is_callback = isinstance(target, types.CallbackQuery)
    if is_callback:
        await target.answer("Формирую отчет мониторинга...")
        msg = target.message
    else:
        msg = await target.answer("⏳ Составляю детальный отчёт мониторинга почты...")

    try:
        report_data = await asyncio.to_thread(monitor.get_monitoring_report, run_fresh_scan)

        user = report_data.get("user") or "Не настроен"
        provider = str(report_data.get("provider", "gmail")).upper()
        server = report_data.get("server", "IMAP SSL")
        total_inbox = report_data.get("total_inbox", 0)
        unread = report_data.get("unread_count", 0)
        dry_run = report_data.get("dry_run", True)
        ai_active = report_data.get("ai_active", False)
        worker_status = report_data.get("worker_status", "ONLINE")
        check_interval = report_data.get("check_interval", 600) // 60
        last_scan = report_data.get("last_scan", {})

        mode_badge = "🛡️ *Симуляция (Dry Run)* — безопасный тест" if dry_run else "⚡ *Боевой режим* — удаление в Корзину"
        ai_badge = "Gemini 3.8 Flash (активен)" if ai_active else "Резервный эвристический фильтр"
        worker_badge = "🟢 ONLINE" if worker_status in ("IDLE", "RUNNING", "ACTIVE", "ONLINE") else f"⚠️ {worker_status}"

        lines = [
            "📋 *Детальный отчёт мониторинга почты*\n",
            f"• 👤 **Почтовый ящик:** `{user}`",
            f"• 🌐 **Сервер подключения:** `{server}` ({provider})",
            f"• 🔄 **Фоновый мониторинг:** {worker_badge} *(периодичность: {check_interval} мин)*",
            f"• 🛡️ **Режим работы:** {mode_badge}",
            f"• 🤖 **AI-классификатор:** `{ai_badge}`\n",
            "📊 *Текущее состояние ящика:*",
            f"• Всего сообщений во Входящих: **{total_inbox:,}**",
            f"• Непрочитанных сообщений: **{unread:,}**\n",
            "⏱️ *Результаты последней проверки:*",
            f"• Время сканирования: `{last_scan.get('timestamp', 'Не выполнялась')}`",
            f"• Проверено новых писем: **{last_scan.get('checked', 0)}**",
            f"• Выявлено спама: **{last_scan.get('spam', 0)}**",
            f"• Важных писем оставлено: **{last_scan.get('important', 0)}**",
        ]

        actions = last_scan.get("actions", [])
        if actions:
            lines.append("\n📝 *Лог последних действий:*")
            for act in actions[:4]:
                lines.append(f"• {act}")
        else:
            lines.append("\n📝 *События:* Спам и подозрительные письма не обнаружены.")

        lines.append("\n_Выберите действие в меню ниже:_")
        text = "\n".join(lines)

        report_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Обновить отчёт", callback_data="mail:report"),
                    InlineKeyboardButton(text="⚡ Экспресс-проверка", callback_data="mail:report:scan"),
                ],
                [
                    InlineKeyboardButton(text="🧹 Очистить спам сейчас", callback_data="mail:clean"),
                    InlineKeyboardButton(text="🔙 Назад в меню почты", callback_data="mail:refresh"),
                ],
            ]
        )

        await msg.edit_text(text, parse_mode="Markdown", reply_markup=report_kb)

    except Exception as e:
        err_msg = f"❌ Ошибка получения отчёта мониторинга: {e}"
        await msg.edit_text(err_msg)


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
            f"• 📈 *Брокер* — портфель Т-Инвестиций, AI-анализ новостей и инвестиционные сигналы (`/broker`).\n"
            f"• 📬 *Управление почтой* — аналитика, топ отправителей, умная сортировка и очистка спама (`/email`).\n"
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
    # BROKER & INVESTMENT ASSISTANT FAST PANEL
    # ==========================================
    @dp.message(Command("broker"))
    @dp.message(F.text == "📈 Брокер")
    async def cmd_broker_menu(message: types.Message):
        user_id = message.from_user.id
        admin_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
        if admin_id and BrokerSecurity and not BrokerSecurity.check_admin_access(user_id, admin_id):
            await message.answer("⛔ *Доступ ограничен:* Раздел «Брокер» защищен и доступен только владельцу бота.")
            return

        status_msg = await message.answer("🔄 Загрузка данных Т-Инвестиций и AI-аналитики...")
        try:
            tc = get_tinkoff_client()
            is_conf = tc.is_configured() if tc else False
            portfolio = await asyncio.to_thread(tc.get_portfolio) if tc else {}
            text = format_broker_menu_text(portfolio, is_conf)
            kb = get_broker_inline_keyboard(is_conf)
            await status_msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка загрузки брокерского пульта: {e}")

    @dp.callback_query(F.data.startswith("brk:"))
    async def handle_broker_callback(query: types.CallbackQuery):
        user_id = query.from_user.id
        admin_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
        if admin_id and BrokerSecurity and not BrokerSecurity.check_admin_access(user_id, admin_id):
            await query.answer("⛔ Доступ ограничен", show_alert=True)
            return

        action = query.data.split(":", 1)[1]
        tc = get_tinkoff_client()
        is_conf = tc.is_configured() if tc else False

        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🎯 Топ предложений", callback_data="brk:ideas"),
                    InlineKeyboardButton(text="🔙 В меню Брокера", callback_data="brk:refresh"),
                ]
            ]
        )

        if action in ("refresh", "menu"):
            await query.answer("Обновление данных...")
            try:
                portfolio = await asyncio.to_thread(tc.get_portfolio) if tc else {}
                text = format_broker_menu_text(portfolio, is_conf)
                kb = get_broker_inline_keyboard(is_conf)
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                await query.answer(f"Ошибка: {e}", show_alert=True)

        elif action == "portfolio":
            await query.answer("Загрузка состава портфеля...")
            try:
                portfolio = await asyncio.to_thread(tc.get_portfolio) if tc else {}
                positions = portfolio.get("positions", [])
                total_val = portfolio.get("total_portfolio_rub", 0.0)
                tot_yield = portfolio.get("expected_yield_rub", 0.0)
                is_demo = portfolio.get("is_demo", False)
                y_sign = "+" if tot_yield >= 0 else ""

                header = "💼 *Состав инвестиционного портфеля:*\n"
                if is_demo:
                    header += "_(Демо-режим: отображаются топ-5 голубых фишек Мосбиржи)_\n"
                header += f"• **Стоимость:** `{total_val:,.2f} ₽`\n• **Доходность:** `{y_sign}{tot_yield:,.2f} ₽`\n\n"

                lines = [header]
                for i, p in enumerate(positions, 1):
                    t = p.get("ticker") or p.get("figi") or "Акция"
                    name = p.get("name") or t
                    qty = p.get("quantity", 0)
                    cur_p = p.get("current_price", 0.0)
                    pos_y = p.get("yield", 0.0)
                    pos_sign = "+" if pos_y >= 0 else ""
                    lines.append(
                        f"**{i}.** 📈 **{name}** (`{t}`)\n"
                        f"   • {qty} шт. × {cur_p:,.2f} ₽ | Доход: `{pos_sign}{pos_y:,.2f} ₽`\n"
                    )

                text = "\n".join(lines)
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🔍 Разобрать акцию", callback_data="brk:search"),
                            InlineKeyboardButton(text="🔙 В меню Брокера", callback_data="brk:refresh"),
                        ]
                    ]
                )
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка загрузки портфеля: {e}", reply_markup=back_kb)

        elif action == "ideas":
            await query.answer("Формирую топ предложений...")
            await query.message.edit_text("⏳ Gemini 3.8 Flash анализирует рынок и отбирает лучшие идеи...")
            try:
                re = get_recommendation_engine()
                ideas = await asyncio.to_thread(re.generate_top_proposals, 4) if re else []
                lines = [
                    "🎯 *ТОП Инвестиционных предложений (Gemini AI)*\n",
                    "_(Сформировано на основе отчетностей, дивидендов и новостного фона)_\n"
                ]
                signal_emojis = {
                    "BUY": "🟢 ПОКУПКА",
                    "ACCUMULATE": "🟡 НАКАПЛИВАТЬ",
                    "HOLD": "⚪ ДЕРЖАТЬ",
                    "TAKE_PROFIT": "🔴 ФИКСАЦИЯ ПРИБЫЛИ",
                }
                for i, idea in enumerate(ideas, 1):
                    sig = signal_emojis.get(idea.get("signal", "BUY"), "🟢 ПОКУПКА")
                    lines.append(
                        f"**{i}. {idea.get('name')}** (`{idea.get('ticker')}`)\n"
                        f"• Сигнал: **{sig}** | Потенциал: **{idea.get('upside', '+15%')}**\n"
                        f"• Горизонт: _{idea.get('horizon', 'Среднесрок')}_\n"
                        f"• Риск: `{idea.get('risk', 'MEDIUM')}`\n"
                        f"💡 _{idea.get('rationale')}_\n"
                    )
                text = "\n".join(lines)
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🔄 Обновить идеи", callback_data="brk:ideas"),
                            InlineKeyboardButton(text="📰 Пульс новостей", callback_data="brk:news"),
                        ],
                        [
                            InlineKeyboardButton(text="🔙 В меню Брокера", callback_data="brk:refresh"),
                        ]
                    ]
                )
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка генерации идей: {e}", reply_markup=back_kb)

        elif action == "news":
            await query.answer("Анализ новостей рынка...")
            await query.message.edit_text("⏳ Считываю ленту РБК / Мосбиржи и оцениваю сентимент через Gemini...")
            try:
                na = get_news_analyzer()
                sentiment_results = await asyncio.to_thread(na.analyze_news_sentiment) if na else []
                lines = ["📰 *AI-Пульс новостей рынка:*\n"]
                sent_emojis = {
                    "BULLISH": "🟢 Позитивно (Бычий)",
                    "BEARISH": "🔴 Негативно (Медвежий)",
                    "NEUTRAL": "⚪ Нейтрально",
                }
                for i, item in enumerate(sentiment_results[:4], 1):
                    sent_badge = sent_emojis.get(item.get("sentiment", "NEUTRAL"), "⚪ Нейтрально")
                    tickers = ", ".join(item.get("affected_tickers", ["Рынок"]))
                    lines.append(
                        f"**{i}.** 📌 *{item.get('title')}*\n"
                        f"• Влияние: {sent_badge} (оценка: `{item.get('score', 0.0)}`)\n"
                        f"• Бумаги: `{tickers}`\n"
                        f"• Разбор: _{item.get('impact')}_\n"
                    )
                text = "\n".join(lines)
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🎯 Топ предложений", callback_data="brk:ideas"),
                            InlineKeyboardButton(text="🔄 Обновить новости", callback_data="brk:news"),
                        ],
                        [
                            InlineKeyboardButton(text="🔙 В меню Брокера", callback_data="brk:refresh"),
                        ]
                    ]
                )
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка анализа новостей: {e}", reply_markup=back_kb)

        elif action == "search":
            _waiting_for_ticker_analysis.add(user_id)
            await query.answer("Ожидание тикера...")
            await query.message.edit_text(
                "🔍 *Глубокий инвестиционный анализ акции*\n\n"
                "Отправьте в чат тикер или название компании (например: `SBER`, `YDEX`, `LKOH`, `T`, `OZON`, `GAZP`, `NVTK`, `PLZL`):\n\n"
                "Gemini AI проанализирует отчетность, дивидендную политику, драйверы роста и риски.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="brk:refresh")]]
                )
            )

        elif action == "token":
            _waiting_for_broker_token.add(user_id)
            await query.answer("Подключение токена...")
            masked = BrokerSecurity.mask_token(tc.token) if tc else "Не настроен"
            instructions = (
                f"🔑 *Безопасное подключение Т-Инвестиций*\n\n"
                f"• Текущий токен: `{masked}`\n"
                f"• Статус защиты: 🛡️ **Только для чтения (Read-Only)**\n\n"
                f"📋 *Как выпустить токен за 1 минуту:*\n"
                f"1. Откройте приложение **«Т-Инвестиции»** (или «Т-Банк»).\n"
                f"2. Перейдите: **Профиль ➔ Настройки**.\n"
                f"3. Пролистайте вниз до раздела **«Токен для OpenAPI»**.\n"
                f"4. Нажмите **«Выпустить токен»**.\n"
                f"5. ⚠️ **ВАЖНО:** Выберите пункт **«Только для чтения» (Read-Only)**!\n\n"
                f"👇 *Отправьте полученный токен в этот чат.*\n"
                f"_(Бот немедленно удалит ваше сообщение из чата для безопасности и сохранит токен в защищенной памяти)_"
            )
            await query.message.edit_text(
                instructions,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="brk:refresh")]]
                )
            )

        elif action == "security":
            await query.answer("Гарантии безопасности")
            sec_text = (
                "🛡️ *Повышенный уровень защиты данных и банка*\n\n"
                "1. **Строго Read-Only (Только чтение):**\n"
                "Бот физически не содержит методов для совершения сделок или вывода денег. "
                "Если токен имеет полные права, система блокирует его использование.\n\n"
                "2. **Удаление сообщений из чата:**\n"
                "При отправке токена в Telegram сообщение пользователя мгновенно удаляется, чтобы секрет не оставался в истории переписки.\n\n"
                "3. **Admin Chat Lock:**\n"
                "Доступ к финансовой аналитике разрешен исключительно подтвержденному Chat ID администратора.\n\n"
                "4. **Zero Credentials Leak:**\n"
                "Токены маскируются (`t.***xyz`) и никогда не записываются в открытые логи или публичный GitHub."
            )
            await query.message.edit_text(sec_text, parse_mode="Markdown", reply_markup=back_kb)

        elif action == "close":
            await query.answer("Раздел закрыт")
            await query.message.delete()

    # ==========================================
    # EMAIL MANAGEMENT FAST PANEL & COMMANDS
    # ==========================================
    @dp.message(Command("email"))
    @dp.message(Command("email_report"))
    @dp.message(F.text == "📬 Управление почтой")
    async def cmd_email_menu(message: types.Message):
        monitor = get_email_monitor()
        if not monitor:
            await message.answer("❌ Модуль управления почтой не найден. Проверьте настройки EMAIL_USER / EMAIL_PASS.")
            return

        text_cmd = (message.text or "").strip()
        parts = text_cmd.split()
        subcmd = parts[1].lower() if len(parts) > 1 else ""
        if subcmd in ("report", "отчет", "отчёт", "monitoring", "мониторинг") or text_cmd.startswith("/email_report"):
            await show_email_monitoring_report(message, monitor)
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

        elif action.startswith("report"):
            run_fresh = (action == "report:scan")
            await show_email_monitoring_report(query, monitor, run_fresh_scan=run_fresh)

        elif action.startswith("all_senders") or action == "senders":
            page = 1
            if ":" in action:
                try:
                    page = int(action.split(":")[1])
                except Exception:
                    page = 1

            await query.answer(f"Загрузка каталога (стр. {page})...")
            if page == 1 and not monitor._all_senders_cache:
                await query.message.edit_text("⏳ Сканирую весь почтовый ящик (первый запуск займет ~15 сек)...")

            try:
                user_id = query.from_user.id
                page_data = await asyncio.to_thread(monitor.get_senders_page, page, 8)
                items = page_data["items"]
                cur_page = page_data["page"]
                total_pages = page_data["total_pages"]
                total_items = page_data["total_items"]

                if not items:
                    text = "👥 *Каталог отправителей:* Писем не найдено."
                    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
                else:
                    _sender_cache[user_id] = {i: s for i, s in enumerate(items, 1)}
                    lines = [
                        f"👥 *Каталог всех отправителей почты*\n"
                        f"• Всего уникальных отправителей: **{total_items}**\n"
                        f"• Страница: **{cur_page} из {total_pages}**\n"
                        f"_(Отсортированы по общему объему писем во Входящих)_\n"
                    ]
                    start_num = (cur_page - 1) * 8
                    for i, s in enumerate(items, 1):
                        global_idx = start_num + i
                        lines.append(f"**{global_idx}.** 👤 **{s['name']}**\n   📧 `{s['email']}` — **{s['total_count']}** писем\n")

                    lines.append("👇 *Нажмите на кнопку отправителя для удаления его писем:*")
                    text = "\n".join(lines)

                    sender_buttons = []
                    row = []
                    for i, s in enumerate(items, 1):
                        s_label = f"🗑️ {i}. {s['name'][:11]}"
                        row.append(InlineKeyboardButton(text=s_label, callback_data=f"sndr:p:{i}"))
                        if len(row) == 2:
                            sender_buttons.append(row)
                            row = []
                    if row:
                        sender_buttons.append(row)

                    # Pagination row
                    nav_row = []
                    if cur_page > 1:
                        nav_row.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"mail:all_senders:{cur_page - 1}"))
                    nav_row.append(InlineKeyboardButton(text=f"📄 {cur_page}/{total_pages}", callback_data=f"mail:all_senders:{cur_page}"))
                    if cur_page < total_pages:
                        nav_row.append(InlineKeyboardButton(text="Вперед ▶", callback_data=f"mail:all_senders:{cur_page + 1}"))
                    sender_buttons.append(nav_row)

                    # Export & search row
                    sender_buttons.append([
                        InlineKeyboardButton(text="📥 Скачать каталог (.txt)", callback_data="mail:download_catalog"),
                        InlineKeyboardButton(text="🔍 Поиск по email", callback_data="sndr:custom"),
                    ])
                    sender_buttons.append([
                        InlineKeyboardButton(text="🔙 Назад к почте", callback_data="mail:refresh"),
                    ])

                    kb = InlineKeyboardMarkup(inline_keyboard=sender_buttons)
                    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                await query.message.edit_text(f"❌ Ошибка сканирования каталога: {e}", reply_markup=back_kb)

        elif action == "download_catalog":
            await query.answer("Формирование полного отчета...")
            status_msg = await query.message.answer("⏳ Составляю файл со всеми отправителями...")
            try:
                report_txt = await asyncio.to_thread(monitor.generate_senders_file_report)
                temp_dir = Path("temp")
                temp_dir.mkdir(exist_ok=True)
                file_path = temp_dir / "all_senders.txt"
                with open(file_path, "w", encoding="utf-8") as fp:
                    fp.write(report_txt)

                doc_file = types.FSInputFile(str(file_path))
                await query.message.answer_document(
                    document=doc_file,
                    caption=(
                        f"📄 *Полный каталог отправителей почтового ящика*\n"
                        f"• Аккаунт: `{monitor.user}`\n"
                        f"• В файле собраны все уникальные отправители и число писем от каждого."
                    ),
                    parse_mode="Markdown"
                )
                await status_msg.delete()
            except Exception as e:
                await status_msg.edit_text(f"❌ Ошибка выгрузки каталога: {e}")

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

        # Check if user is sending a broker token
        if user_id in _waiting_for_broker_token or text.startswith("t."):
            _waiting_for_broker_token.discard(user_id)
            # Instantly delete user's message containing token from Telegram chat for safety
            try:
                await message.delete()
            except Exception:
                pass

            admin_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
            if admin_id and BrokerSecurity and not BrokerSecurity.check_admin_access(user_id, admin_id):
                await message.answer("⛔ Только администратор может подключать токен Т-Инвестиций.")
                return

            status_msg = await message.answer("🔐 Проверка и безопасная привязка токена Т-Инвестиций...")
            tc = get_tinkoff_client()
            if not tc:
                await status_msg.edit_text("❌ Модуль брокера недоступен.")
                return

            res = await asyncio.to_thread(tc.set_token, text)
            if not res.get("success"):
                await status_msg.edit_text(f"❌ {res.get('error')}")
                return

            masked = res.get("masked_token")
            acc_name = res.get("account_name")
            await status_msg.edit_text(
                f"✅ *Токен Т-Инвестиций успешно подключен!*\n\n"
                f"• **Токен:** `{masked}`\n"
                f"• **Счёт:** `{acc_name}`\n"
                f"• **Режим защиты:** 🔒 *Только чтение (Read-Only)*\n"
                f"• Ваше сообщение с токеном было автоматически удалено из чата для безопасности.\n\n"
                f"Теперь вам доступен реальный анализ вашего портфеля!",
                parse_mode="Markdown",
                reply_markup=get_broker_inline_keyboard(is_configured=True),
            )
            return

        # Check if user is searching for a ticker analysis
        if user_id in _waiting_for_ticker_analysis:
            _waiting_for_ticker_analysis.discard(user_id)
            ticker = text.strip().upper()
            status_msg = await message.answer(f"🔍 Запуск AI-анализа акции `{ticker}`...")
            re = get_recommendation_engine()
            if not re:
                await status_msg.edit_text("❌ Модуль аналитики недоступен.")
                return

            analysis = await asyncio.to_thread(re.analyze_ticker, ticker)
            name = analysis.get("name", ticker)
            sector = analysis.get("sector", "Рынок РФ")
            sig = analysis.get("signal", "BUY")
            sig_badge = "🟢 ПОКУПКА" if sig == "BUY" else ("🟡 НАКАПЛИВАТЬ" if sig == "ACCUMULATE" else "⚪ ДЕРЖАТЬ")
            upside = analysis.get("upside", "+15%")
            horizon = analysis.get("horizon", "3-6 мес")
            summary = analysis.get("summary", "")
            drivers = analysis.get("drivers", [])
            risks = analysis.get("risks", [])

            lines = [
                f"📊 *Инвестиционный анализ: {name}* (`{ticker}`)\n",
                f"• **Сектор:** {sector}",
                f"• **Сигнал:** **{sig_badge}**",
                f"• **Ожидаемый потенциал:** `{upside}`",
                f"• **Горизонт инвестирования:** _{horizon}_\n",
                f"📝 *Вывод аналитика:*\n_{summary}_\n",
            ]
            if drivers:
                lines.append("🚀 *Ключевые драйверы роста:*")
                for d in drivers:
                    lines.append(f"• {d}")
            if risks:
                lines.append("\n⚠️ *Факторы риска:*")
                for r in risks:
                    lines.append(f"• {r}")

            lines.append("\n_Не является индивидуальной инвестиционной рекомендацией._")
            card_text = "\n".join(lines)

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🎯 Топ предложений", callback_data="brk:ideas"),
                        InlineKeyboardButton(text="🔍 Другой тикер", callback_data="brk:search"),
                    ],
                    [
                        InlineKeyboardButton(text="🔙 В меню Брокера", callback_data="brk:refresh"),
                    ],
                ]
            )
            await status_msg.edit_text(card_text, parse_mode="Markdown", reply_markup=kb)
            return

        if text.startswith("/") or text in ["📊 Статус процессов", "📸 Снимок экрана", "⚡ Проверка связи", "🧹 Очистить кэш", "📬 Управление почтой", "📈 Брокер"]:
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
