"""
Cloud Email Monitoring & Autonomous Spam Cleaner Module.
Uses IMAP SSL and Gemini AI to classify and filter emails safely.
"""
import imaplib
import email
from email.header import decode_header
import json
import time
from typing import Any
from google import genai
from google.genai import types

try:
    from . import config as cfg
except (ImportError, ValueError):
    try:
        import cloud.config as cfg
    except (ImportError, ValueError):
        import config as cfg

try:
    from tg_bot.notifier import tg_notifier
    from tg_bot.process_registry import process_registry
except ImportError:
    # Resilient fallback if tg_bot is not installed or missing
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

        def request_confirmation(self, title: str, description: str, timeout_sec: int = 120) -> bool:
            print(f"[FallbackNotifier] Confirmation requested for: {title}")
            return True

    tg_notifier = _FallbackNotifier()


def decode_str(header_val: Any) -> str:
    """Decodes MIME encoded email header fields into clean unicode text."""
    if not header_val:
        return ""
    decoded_fragments = decode_header(header_val)
    parts = []
    for frag, enc in decoded_fragments:
        if isinstance(frag, bytes):
            try:
                parts.append(frag.decode(enc or "utf-8", errors="replace"))
            except Exception:
                parts.append(frag.decode("latin1", errors="replace"))
        else:
            parts.append(str(frag))
    return "".join(parts).strip()


def extract_body(msg: email.message.Message) -> str:
    """Extracts plain text body from email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body += payload.decode(charset, errors="replace") + "\n"
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
        except Exception:
            pass
    return body.strip()


class EmailMonitor:
    def __init__(self):
        self.server = cfg.EMAIL_IMAP_SERVER
        self.port = cfg.EMAIL_IMAP_PORT
        self.user = cfg.EMAIL_USER
        self.password = cfg.EMAIL_PASS
        self.dry_run = cfg.EMAIL_DRY_RUN
        self.require_confirm = cfg.REQUIRE_CONFIRMATION

        # Gemini AI client
        self.ai_client: genai.Client | None = None
        if cfg.GEMINI_API_KEY:
            try:
                self.ai_client = genai.Client(api_key=cfg.GEMINI_API_KEY)
            except Exception as e:
                print(f"[Email Monitor] Warning: AI client init error: {e}")

    def connect(self) -> imaplib.IMAP4_SSL:
        """Establishes an SSL connection to the IMAP server."""
        if not self.user or not self.password:
            raise ValueError("Не заданы EMAIL_USER или EMAIL_PASS в .env!")
        if not self.server:
            raise ValueError("Не указан IMAP сервер! Проверьте EMAIL_PROVIDER или EMAIL_IMAP_SERVER.")

        client = imaplib.IMAP4_SSL(self.server, self.port)
        client.login(self.user, self.password)
        return client

    def get_mailbox_stats(self) -> dict[str, Any]:
        """Returns overview stats of the mailbox."""
        imap = self.connect()
        try:
            status, counts = imap.select("INBOX", readonly=True)
            total = int(counts[0].decode("utf-8")) if counts and counts[0] else 0
            status, unread = imap.search(None, "UNSEEN")
            unread_count = len(unread[0].split()) if unread and unread[0] else 0
            return {
                "user": self.user,
                "provider": getattr(cfg, "EMAIL_PROVIDER", "gmail"),
                "total": total,
                "unread": unread_count,
                "dry_run": self.dry_run,
                "ai_active": self.ai_client is not None,
            }
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def get_top_senders(self, limit: int = 8, scan_batch: int = 30) -> list[dict[str, Any]]:
        """Scans recent unread messages and aggregates top senders via bulk fetch."""
        from collections import Counter
        imap = self.connect()
        try:
            imap.select("INBOX", readonly=True)
            status, unread = imap.search(None, "UNSEEN")
            if status != "OK" or not unread[0]:
                return []
            ids = unread[0].split()
            batch = ids[-scan_batch:]
            res, data = imap.fetch(b",".join(batch), "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            senders = []
            if res == "OK":
                for item in data:
                    if isinstance(item, tuple) and len(item) == 2:
                        msg = email.message_from_bytes(item[1])
                        s = decode_str(msg.get("From", ""))
                        if s:
                            senders.append(s)
            counts = Counter(senders).most_common(limit)
            return [{"sender": s, "count": c} for s, c in counts]
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def get_recent_emails(self, limit: int = 5) -> list[dict[str, str]]:
        """Fetches metadata for the most recent unread incoming emails via bulk fetch."""
        imap = self.connect()
        try:
            imap.select("INBOX", readonly=True)
            status, unread = imap.search(None, "UNSEEN")
            if status != "OK" or not unread[0]:
                return []
            ids = unread[0].split()
            batch = ids[-limit:]
            res, data = imap.fetch(b",".join(batch), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            results = []
            if res == "OK":
                for item in data:
                    if isinstance(item, tuple) and len(item) == 2:
                        msg = email.message_from_bytes(item[1])
                        sender = decode_str(msg.get("From", "")) or "Неизвестно"
                        subject = decode_str(msg.get("Subject", "")) or "Без темы"
                        date = decode_str(msg.get("Date", ""))[:22]
                        results.append({"sender": sender, "subject": subject, "date": date})
            results.reverse()
            return results
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def smart_sort_unseen(self, limit: int = 15) -> dict[str, list[dict[str, str]]]:
        """Categorizes recent unread emails into Important, Newsletters, Social, and Spam via bulk fetch."""
        categories: dict[str, list[dict[str, str]]] = {
            "IMPORTANT": [],
            "NEWSLETTER": [],
            "SOCIAL": [],
            "SPAM": [],
        }
        imap = self.connect()
        try:
            imap.select("INBOX", readonly=True)
            status, unread = imap.search(None, "UNSEEN")
            if status != "OK" or not unread[0]:
                return categories
            ids = unread[0].split()
            batch = ids[-limit:]
            res, data = imap.fetch(b",".join(batch), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if res == "OK":
                for item in data:
                    if isinstance(item, tuple) and len(item) == 2:
                        msg = email.message_from_bytes(item[1])
                        sender = decode_str(msg.get("From", ""))
                        subject = decode_str(msg.get("Subject", ""))
                        s_lower = (sender + " " + subject).lower()

                        if any(k in s_lower for k in ["казино", "выигрыш", "crypto bonus", "viagra", "миллион", "jackpot", "займ"]):
                            cat = "SPAM"
                        elif any(k in s_lower for k in ["patreon", "discord", "youtube", "vk", "telegram", "github", "twitter", "reddit", "steam"]):
                            cat = "SOCIAL"
                        elif any(k in s_lower for k in ["ozon", "скидк", "распродаж", "акци", "заказ", "wildberries", "market", "яндекс", "пятерочка", "aliexpress", "tldr"]):
                            cat = "NEWSLETTER"
                        else:
                            cat = "IMPORTANT"

                        categories[cat].append({"sender": sender[:35], "subject": subject[:50]})
            return categories
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def toggle_dry_run(self) -> bool:
        """Toggles dry run mode."""
        self.dry_run = not self.dry_run
        return self.dry_run

    def classify_email_with_ai(self, sender: str, subject: str, snippet: str) -> dict[str, Any]:
        """Classifies email using Gemini AI into SPAM, PHISHING, NEWSLETTER, or IMPORTANT."""
        if not self.ai_client:
            # Fallback simple heuristic if AI client is unavailable
            is_spam = any(k in (subject + snippet).lower() for k in ["казино", "выигрыш", "заработок", "crypto bonus", "viagra"])
            return {
                "is_spam": is_spam,
                "category": "SPAM" if is_spam else "IMPORTANT",
                "confidence": 0.8 if is_spam else 0.5,
                "reason": "Эвристический фильтр",
                "recommended_action": "MOVE_TO_SPAM" if is_spam else "KEEP",
            }

        prompt = (
            "Проанализируй входящее электронное письмо и определи, является ли оно спамом, "
            "фишингом, рекламной рассылкой или важным сообщением.\n\n"
            f"Отправитель: {sender}\n"
            f"Тема: {subject}\n"
            f"Текст письма (фрагмент):\n{snippet[:1000]}\n\n"
            "Верни ответ СТРОГО в формате JSON со следующими полями:\n"
            "{\n"
            '  "is_spam": true/false,\n'
            '  "category": "SPAM" | "PHISHING" | "NEWSLETTER" | "IMPORTANT",\n'
            '  "confidence": float_from_0_to_1,\n'
            '  "reason": "краткая причина на русском языке",\n'
            '  "recommended_action": "MOVE_TO_TRASH" | "MOVE_TO_SPAM" | "KEEP"\n'
            "}"
        )

        try:
            response = self.ai_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            raw_text = response.text.strip()
            # Clean possible markdown fence
            if raw_text.startswith("```"):
                raw_text = raw_text.strip("`").replace("json\n", "", 1)
            return json.loads(raw_text)
        except Exception as e:
            print(f"[AI Classification Error]: {e}")
            # Fallback heuristic if AI quota is reached or network fails
            text_corpus = (subject + " " + snippet).lower()
            is_spam = any(k in text_corpus for k in ["казино", "выигрыш", "миллион", "заработок", "crypto", "casino", "prize", "winner", "jackpot"])
            return {
                "is_spam": is_spam,
                "category": "SPAM" if is_spam else "UNKNOWN",
                "confidence": 0.85 if is_spam else 0.0,
                "reason": f"Эвристический фильтр безопасности (резервный режим)",
                "recommended_action": "MOVE_TO_TRASH" if is_spam else "KEEP",
            }

    def process_unread_emails(self) -> dict[str, Any]:
        """Scans unread emails, analyzes them, and cleans spam."""
        process_registry.update("email_cleaner", status="RUNNING", details="Проверка новых писем...")
        stats = {"checked": 0, "spam": 0, "important": 0, "actions": []}

        try:
            imap = self.connect()
        except Exception as e:
            process_registry.update("email_cleaner", status="ERROR", details=f"Ошибка входа: {e}")
            tg_notifier.send_message(f"⚠️ *Ошибка подключения к почте:* {e}")
            return stats

        try:
            imap.select("INBOX")
            # Search for unread messages
            status, messages = imap.search(None, "UNSEEN")
            if status != "OK" or not messages[0]:
                process_registry.update("email_cleaner", status="IDLE", details="Новых писем нет")
                return stats

            msg_ids = messages[0].split()
            total_unseen = len(msg_ids)
            # Process up to 10 latest unread emails per scan to respect rate limits and keep response snappy
            batch = msg_ids[-10:]
            stats["checked"] = len(batch)
            stats["total_unseen"] = total_unseen

            for mid in batch:
                res, data = imap.fetch(mid, "(RFC822)")
                if res != "OK":
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                sender = decode_str(msg.get("From", ""))
                subject = decode_str(msg.get("Subject", ""))
                body = extract_body(msg)

                analysis = self.classify_email_with_ai(sender, subject, body[:800])
                category = analysis.get("category", "UNKNOWN")
                is_spam = analysis.get("is_spam", False)
                reason = analysis.get("reason", "")
                action = analysis.get("recommended_action", "KEEP")

                if is_spam or action in ("MOVE_TO_TRASH", "MOVE_TO_SPAM"):
                    stats["spam"] += 1
                    report_entry = f"🗑 *Спам:* '{subject[:40]}' от {sender[:30]} ({reason})"
                    stats["actions"].append(report_entry)

                    if self.dry_run:
                        # Safe test mode: do not delete
                        stats["actions"].append("  *(Режим симуляции: письмо не удалено)*")
                    else:
                        # Move to Trash or mark Deleted
                        try:
                            # Try moving to Trash or mark as Deleted
                            imap.store(mid, "+FLAGS", "\\Deleted")
                        except Exception as e:
                            print(f"Error marking email {mid} as deleted: {e}")
                else:
                    stats["important"] += 1

            if not self.dry_run:
                imap.expunge()

            # Update process registry
            details = f"Проверено: {stats['checked']} | Спам: {stats['spam']} | Важные: {stats['important']}"
            process_registry.update("email_cleaner", status="IDLE", details=details)

            # Send summary to Telegram if any emails were checked
            if stats["checked"] > 0:
                summary = (
                    f"📬 *Отчёт мониторинга почты:*\n"
                    f"• Проверено новых писем: `{stats['checked']}`\n"
                    f"• Выявлено спама: `{stats['spam']}`\n"
                    f"• Оставлено во Входящих: `{stats['important']}`\n"
                )
                if stats["actions"]:
                    summary += "\n" + "\n".join(stats["actions"][:5])
                tg_notifier.send_message(summary)

        finally:
            try:
                imap.close()
                imap.logout()
            except Exception:
                pass

        return stats
