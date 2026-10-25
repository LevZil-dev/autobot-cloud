"""
AutoBot Cloud Web Service (FastAPI + aiogram + Email Monitor).
Runs 24/7 on Render Free Tier. Provides health checks, Telegram bot, and email cleaner.
"""
import os
import sys
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

# Add current folder and parent folder to sys.path
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for p in [str(CURRENT_DIR), str(PARENT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

# Flexible imports supporting both root-level and package-level repository layouts
try:
    from . import config as cfg
    from .email_monitor import EmailMonitor
except (ImportError, ValueError):
    try:
        import cloud.config as cfg
        from cloud.email_monitor import EmailMonitor
    except (ImportError, ValueError):
        import config as cfg
        from email_monitor import EmailMonitor

try:
    from tg_bot.process_registry import process_registry
    from tg_bot.notifier import tg_notifier
    from tg_bot.bot_service import create_dispatcher
except ImportError:
    import json
    class _DummyProcessRegistry:
        def __init__(self):
            self.processes = {}
        def update(self, name, status, details=""):
            self.processes[name] = {"status": status, "details": details, "updated_at": time.time()}
        def get(self, *args, **kwargs):
            return {}
        def get_all(self):
            return self.processes

    process_registry = _DummyProcessRegistry()

    class _FallbackNotifier:
        def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
            print(f"[Telegram Notification]: {text}")
            token = getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = getattr(cfg, "TELEGRAM_ADMIN_CHAT_ID", "") or os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
            if token and chat_id:
                try:
                    import urllib.request
                    url = f"https://api.telegram.org/bot{token}/sendMessage"
                    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": parse_mode}).encode("utf-8")
                    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        return r.status == 200
                except Exception as ex:
                    print(f"[FallbackNotifier Warning]: {ex}")
            return False

        async def send_message_async(self, text: str, parse_mode: str = "Markdown") -> bool:
            return self.send_message(text, parse_mode)

    tg_notifier = _FallbackNotifier()
    create_dispatcher = None

START_TIME = time.time()
bot_instance = None
bot_dp = None
bg_tasks = []


async def email_worker_loop():
    """Background periodic email checker."""
    monitor = EmailMonitor()
    print("[Cloud Worker] Фоновый цикл проверки почты запущен.")
    while True:
        try:
            if cfg.EMAIL_USER and cfg.EMAIL_PASS:
                stats = await asyncio.to_thread(monitor.process_unread_emails)
                print(f"[Cloud Worker] Проверка почты: {stats}")
            else:
                process_registry.update("email_cleaner", status="IDLE", details="Ожидание EMAIL_USER в .env")
        except Exception as e:
            print(f"[Cloud Worker Error]: {e}")
            process_registry.update("email_cleaner", status="ERROR", details=str(e))
        await asyncio.sleep(cfg.CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_instance, bot_dp
    if os.getenv("TEST_MODE") == "1":
        yield
        return

    print("🚀 [Render Startup] Запуск облачных сервисов AutoBot...")

    email_mon = None
    try:
        email_mon = EmailMonitor()
    except Exception as em_err:
        print(f"⚠️ [Render] EmailMonitor init warning: {em_err}")

    # 1. Start Telegram Bot if token configured and dispatcher available
    if cfg.TELEGRAM_BOT_TOKEN and create_dispatcher is not None:
        try:
            bot_instance, bot_dp = await create_dispatcher(email_monitor=email_mon)
            t_bot = asyncio.create_task(bot_dp.start_polling(bot_instance))
            bg_tasks.append(t_bot)
            print("✅ [Render] Telegram Hub успешно запущен в фоне.")
        except Exception as e:
            print(f"❌ [Render] Ошибка запуска Telegram бота: {e}")

    # 2. Start Email Cleaner loop
    t_email = asyncio.create_task(email_worker_loop())
    bg_tasks.append(t_email)

    # 3. Send Telegram notification on successful boot
    if cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_ADMIN_CHAT_ID:
        asyncio.create_task(
            tg_notifier.send_message_async(
                "☁️ *AutoBot Cloud успешно запущен на Render!*\n"
                "• Платформа: Render (Free Tier)\n"
                "• Статус: 🟢 Онлайн 24/7\n"
                "• Почта: `" + (cfg.EMAIL_USER or "не настроена") + "`\n"
                "• Режим: " + ("Симуляция (Dry Run)" if cfg.EMAIL_DRY_RUN else "Активная очистка")
            )
        )

    yield

    print("🛑 [Render Shutdown] Остановка облачных сервисов...")
    for task in bg_tasks:
        task.cancel()
    if bot_instance:
        await bot_instance.session.close()


app = FastAPI(title="AutoBot Cloud Hub", lifespan=lifespan)


@app.get("/")
async def root():
    uptime = int(time.time() - START_TIME)
    return {
        "service": "AutoBot Cloud Controller",
        "status": "ONLINE",
        "uptime_seconds": uptime,
        "email_configured": bool(cfg.EMAIL_USER and cfg.EMAIL_PASS),
        "telegram_configured": bool(cfg.TELEGRAM_BOT_TOKEN),
    }


@app.get("/health")
async def health():
    """Healthcheck endpoint for Render and external 24/7 keep-alive pings."""
    return {"status": "ok", "timestamp": time.time()}


@app.get("/check-email")
async def check_email_now():
    """Manual trigger to scan and clean emails immediately."""
    if not cfg.EMAIL_USER or not cfg.EMAIL_PASS:
        return JSONResponse(status_code=400, content={"error": "EMAIL_USER or EMAIL_PASS not set in environment"})
    monitor = EmailMonitor()
    stats = await asyncio.to_thread(monitor.process_unread_emails)
    return {"status": "success", "stats": stats}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
