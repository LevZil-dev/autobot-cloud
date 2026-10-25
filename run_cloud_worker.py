"""
Autonomous 24/7 Cloud Worker.
Runs continuous email monitoring and periodic task scheduling.
"""
import sys
import time
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import cloud.config as cfg
from cloud.email_monitor import EmailMonitor
from tg_bot.process_registry import process_registry
from tg_bot.notifier import tg_notifier


def main():
    print("\n" + "=" * 55)
    print("☁️ AutoBot Cloud Worker — 24/7 Monitoring Daemon")
    print(f"📧 Мониторинг почты: {cfg.EMAIL_USER or '(не настроена в .env)'}")
    print(f"⏱ Интервал проверки: {cfg.CHECK_INTERVAL_SECONDS} сек.")
    print(f"🛡 Безопасный режим (Dry Run): {'ВКЛ' if cfg.EMAIL_DRY_RUN else 'ВЫКЛ'}")
    print("=" * 55 + "\n")

    monitor = EmailMonitor()
    process_registry.update("email_cleaner", status="ONLINE", details="Облачный воркер активен")

    # Notify admin via Telegram on startup
    tg_notifier.send_message(
        "🚀 *AutoBot Cloud Worker запущен!*\n"
        f"• Сервер: Облако / 24/7\n"
        f"• Почта: `{cfg.EMAIL_USER or 'не настроена'}`\n"
        f"• Режим: `{'Симуляция (Dry Run)' if cfg.EMAIL_DRY_RUN else 'Очистка'}`"
    )

    while True:
        try:
            if cfg.EMAIL_USER and cfg.EMAIL_PASS:
                stats = monitor.process_unread_emails()
                print(f"[{time.strftime('%H:%M:%S')}] Проверка завершена: {stats}")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Ожидание настройки EMAIL_USER и EMAIL_PASS в .env...")
                process_registry.update("email_cleaner", status="IDLE", details="Ожидание логина/пароля в .env")

            time.sleep(cfg.CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nОстановка облачного воркера...")
            process_registry.update("email_cleaner", status="STOPPED", details="Остановлен пользователем")
            break
        except Exception as e:
            print(f"Ошибка в цикле воркера: {e}")
            process_registry.update("email_cleaner", status="ERROR", details=str(e))
            time.sleep(60)


if __name__ == "__main__":
    main()
