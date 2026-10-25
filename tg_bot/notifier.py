"""
Telegram Notifier & Confirmation Dispatcher.
Allows any background agent or subagent to send alerts and request user approval via Telegram.
"""
import os
import asyncio
from typing import Callable, Any
from pathlib import Path
from dotenv import load_dotenv, set_key

import httpx

DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class TelegramNotifier:
    def __init__(self):
        load_dotenv(DOTENV_PATH)
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.admin_chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
        self.pending_confirmations: dict[str, dict[str, Any]] = {}

    def set_admin_chat_id(self, chat_id: str | int):
        """Updates and persists the admin chat ID in .env."""
        self.admin_chat_id = str(chat_id)
        os.environ["TELEGRAM_ADMIN_CHAT_ID"] = str(chat_id)
        try:
            set_key(str(DOTENV_PATH), "TELEGRAM_ADMIN_CHAT_ID", str(chat_id))
        except Exception:
            pass

    async def send_message_async(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Sends a text message asynchronously via Telegram Bot API."""
        if not self.token or not self.admin_chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.admin_chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            print(f"[TG Notifier Error] {e}")
            return False

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Synchronous wrapper for sending a message."""
        if not self.token or not self.admin_chat_id:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.admin_chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            print(f"[TG Notifier Error] {e}")
            return False

    async def send_photo_async(self, photo_path: str, caption: str = "") -> bool:
        """Sends an image file asynchronously."""
        if not self.token or not self.admin_chat_id or not os.path.isfile(photo_path):
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                with open(photo_path, "rb") as f:
                    files = {"photo": f}
                    data = {"chat_id": self.admin_chat_id, "caption": caption}
                    resp = await client.post(url, data=data, files=files)
                    return resp.status_code == 200
        except Exception as e:
            print(f"[TG Photo Error] {e}")
            return False

    async def request_confirmation(
        self,
        action_id: str,
        prompt: str,
        on_confirm: Callable[[], Any] | None = None,
        on_reject: Callable[[], Any] | None = None,
    ) -> bool:
        """Sends an interactive confirmation prompt with inline buttons."""
        if not self.token or not self.admin_chat_id:
            return False

        self.pending_confirmations[action_id] = {
            "prompt": prompt,
            "on_confirm": on_confirm,
            "on_reject": on_reject,
        }

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Разрешить", "callback_data": f"confirm:{action_id}"},
                    {"text": "❌ Отклонить", "callback_data": f"reject:{action_id}"},
                ]
            ]
        }
        payload = {
            "chat_id": self.admin_chat_id,
            "text": f"⚠️ *ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ:*\n\n{prompt}",
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            print(f"[TG Confirmation Error] {e}")
            return False

    def handle_decision(self, action_id: str, approved: bool) -> str:
        """Handles user decision from Telegram callback."""
        if action_id not in self.pending_confirmations:
            return "Действие уже выполнено или устарело."

        entry = self.pending_confirmations.pop(action_id)
        callback = entry["on_confirm"] if approved else entry["on_reject"]

        if callback:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback())
                else:
                    callback()
            except Exception as e:
                return f"Ошибка при выполнении: {e}"

        return "Действие разрешено ✅" if approved else "Действие отклонено ❌"


# Global singleton
tg_notifier = TelegramNotifier()
