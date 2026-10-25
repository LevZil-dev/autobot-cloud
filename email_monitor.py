"""
Cloud Email Monitoring & Autonomous Spam Cleaner Module.
Uses IMAP SSL and Gemini AI to classify and filter emails safely.
"""
import imaplib
import email
from email.header import decode_header
import json
import time
import os
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

        # In-memory cache for all senders catalog
        self._all_senders_cache: list[dict[str, Any]] = []
        self._all_senders_cache_time: float = 0.0

        # Monitoring and reporting cache
        self.last_scan_info: dict[str, Any] = {
            "timestamp": "Ожидание первого сканирования",
            "checked": 0,
            "spam": 0,
            "important": 0,
            "total_unseen": 0,
            "actions": [],
        }

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

    def get_tracked_senders(self, scan_batch: int = 50, limit: int = 10) -> list[dict[str, Any]]:
        """Scans recent unread messages and aggregates full sender details (name, email, unread count)."""
        from email.utils import parseaddr
        imap = self.connect()
        try:
            imap.select("INBOX", readonly=True)
            status, unread = imap.search(None, "UNSEEN")
            if status != "OK" or not unread[0]:
                return []
            ids = unread[0].split()
            batch = ids[-scan_batch:]
            res, data = imap.fetch(b",".join(batch), "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            senders_map: dict[str, dict[str, Any]] = {}
            if res == "OK":
                for item in data:
                    if isinstance(item, tuple) and len(item) == 2:
                        msg = email.message_from_bytes(item[1])
                        raw_from = msg.get("From", "")
                        name, addr = parseaddr(raw_from)
                        addr = addr.lower().strip()
                        clean_name = decode_str(name).strip() or (addr.split("@")[0] if "@" in addr else addr)
                        if addr:
                            if addr not in senders_map:
                                senders_map[addr] = {
                                    "name": clean_name,
                                    "email": addr,
                                    "unread": 0,
                                    "domain": addr.split("@")[-1] if "@" in addr else "",
                                }
                            senders_map[addr]["unread"] += 1

            sorted_senders = sorted(senders_map.values(), key=lambda x: x["unread"], reverse=True)
            return sorted_senders[:limit]
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def get_sender_details(self, sender_email: str) -> dict[str, Any]:
        """Gets exact total and unread email count for a specific sender address or domain."""
        imap = self.connect()
        try:
            imap.select("INBOX", readonly=True)
            status, all_matches = imap.search(None, "FROM", f'"{sender_email}"')
            all_ids = all_matches[0].split() if all_matches and all_matches[0] else []

            status, unread_matches = imap.search(None, "UNSEEN", "FROM", f'"{sender_email}"')
            unread_ids = unread_matches[0].split() if unread_matches and unread_matches[0] else []

            return {
                "sender": sender_email,
                "total": len(all_ids),
                "unread": len(unread_ids),
            }
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def scan_all_senders(self, force_refresh: bool = False, max_messages: int = 15000) -> list[dict[str, Any]]:
        """Scans the entire INBOX and returns all unique senders sorted by message volume."""
        from email.utils import parseaddr

        if not force_refresh and self._all_senders_cache and (time.time() - self._all_senders_cache_time < 300):
            return self._all_senders_cache

        imap = self.connect()
        try:
            imap.select("INBOX", readonly=True)
            status, counts = imap.search(None, "ALL")
            if status != "OK" or not counts[0]:
                return []

            ids = counts[0].split()
            target_ids = ids[-max_messages:]

            senders_map: dict[str, dict[str, Any]] = {}
            chunk_size = 500
            for i in range(0, len(target_ids), chunk_size):
                chunk = target_ids[i : i + chunk_size]
                res, data = imap.fetch(b",".join(chunk), "(BODY.PEEK[HEADER.FIELDS (FROM)])")
                if res == "OK":
                    for item in data:
                        if isinstance(item, tuple) and len(item) == 2:
                            msg = email.message_from_bytes(item[1])
                            raw_from = msg.get("From", "")
                            name, addr = parseaddr(raw_from)
                            addr = addr.lower().strip()
                            clean_name = decode_str(name).strip() or (addr.split("@")[0] if "@" in addr else addr)
                            if addr:
                                if addr not in senders_map:
                                    senders_map[addr] = {
                                        "name": clean_name,
                                        "email": addr,
                                        "total_count": 0,
                                        "domain": addr.split("@")[-1] if "@" in addr else "",
                                    }
                                senders_map[addr]["total_count"] += 1

            sorted_senders = sorted(senders_map.values(), key=lambda x: x["total_count"], reverse=True)
            self._all_senders_cache = sorted_senders
            self._all_senders_cache_time = time.time()
            return sorted_senders
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def get_senders_page(self, page: int = 1, page_size: int = 8, query: str = "") -> dict[str, Any]:
        """Returns paginated senders list."""
        all_senders = self.scan_all_senders(force_refresh=False)
        if query:
            q = query.lower().strip()
            all_senders = [s for s in all_senders if q in s["name"].lower() or q in s["email"].lower()]

        total_items = len(all_senders)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))

        start = (page - 1) * page_size
        items = all_senders[start : start + page_size]
        return {
            "items": items,
            "page": page,
            "total_pages": total_pages,
            "total_items": total_items,
        }

    def generate_senders_file_report(self) -> str:
        """Generates a complete text catalog of all senders stored in the mailbox."""
        senders = self.scan_all_senders(force_refresh=False)
        lines = [
            f"===========================================================",
            f" ПОЛНЫЙ КАТАЛОГ ВСЕХ ОТПРАВИТЕЛЕЙ ПОЧТОВОГО ЯЩИКА",
            f" Аккаунт: {self.user}",
            f" Всего уникальных отправителей: {len(senders)}",
            f" Дата формирования: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"===========================================================\n",
        ]
        for idx, s in enumerate(senders, 1):
            lines.append(f"{idx:4d}. {s['name']:<35} | {s['email']:<40} | Писем: {s['total_count']}")
        return "\n".join(lines)

    def delete_all_from_sender(self, sender_query: str, only_unread: bool = False, force: bool = False) -> dict[str, Any]:
        """Deletes (moves to Trash) all emails from a specific sender or domain."""
        imap = self.connect()
        try:
            imap.select("INBOX")
            if only_unread:
                status, matches = imap.search(None, "UNSEEN", "FROM", f'"{sender_query}"')
            else:
                status, matches = imap.search(None, "FROM", f'"{sender_query}"')

            ids = matches[0].split() if matches and matches[0] else []
            count = len(ids)

            if count == 0:
                return {
                    "success": True,
                    "count": 0,
                    "sender": sender_query,
                    "message": f"Писем от '{sender_query}' не найдено.",
                }

            if self.dry_run and not force:
                return {
                    "success": True,
                    "count": count,
                    "dry_run": True,
                    "sender": sender_query,
                    "message": f"Найдено {count} писем от '{sender_query}'. (Режим симуляции: письма не удалены).",
                }

            # Batch delete in chunks of 100
            for i in range(0, count, 100):
                chunk = ids[i : i + 100]
                chunk_bytes = b",".join(chunk)
                try:
                    imap.store(chunk_bytes, "+X-GM-LABELS", "\\Trash")
                except Exception:
                    pass
                imap.store(chunk_bytes, "+FLAGS", "\\Deleted")

            imap.expunge()
            return {
                "success": True,
                "count": count,
                "dry_run": False,
                "sender": sender_query,
                "message": f"Успешно перемещено в Корзину {count} писем от '{sender_query}'!",
            }
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

    def get_monitoring_report(self, run_scan: bool = False) -> dict[str, Any]:
        """
        Generates a comprehensive email monitoring report.
        If run_scan is True, performs a fresh scan of unread emails.
        """
        if run_scan:
            self.process_unread_emails(send_tg_notification=False)

        stats = self.get_mailbox_stats()
        proc = process_registry.get("email_cleaner") if hasattr(process_registry, "get") else {}
        worker_status = proc.get("status", "ONLINE") if isinstance(proc, dict) else "ONLINE"
        worker_details = proc.get("details", "") if isinstance(proc, dict) else ""

        return {
            "user": self.user,
            "provider": getattr(cfg, "EMAIL_PROVIDER", "gmail"),
            "server": f"{self.server}:{self.port}",
            "total_inbox": stats.get("total", 0),
            "unread_count": stats.get("unread", 0),
            "dry_run": self.dry_run,
            "ai_active": self.ai_client is not None,
            "worker_status": worker_status,
            "worker_details": worker_details,
            "check_interval": getattr(cfg, "CHECK_INTERVAL_SECONDS", 600),
            "last_scan": self.last_scan_info,
        }

    def process_unread_emails(self, send_tg_notification: bool = False) -> dict[str, Any]:
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
                self.last_scan_info = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "checked": 0,
                    "spam": 0,
                    "important": 0,
                    "total_unseen": 0,
                    "actions": [],
                }
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
                            imap.store(mid, "+FLAGS", "\\Deleted")
                        except Exception as e:
                            print(f"Error marking email {mid} as deleted: {e}")
                else:
                    stats["important"] += 1

            if not self.dry_run:
                imap.expunge()

            # Record to last_scan_info cache
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            self.last_scan_info = {
                "timestamp": now_str,
                "checked": stats["checked"],
                "spam": stats["spam"],
                "important": stats["important"],
                "total_unseen": stats.get("total_unseen", 0),
                "actions": list(stats["actions"]),
            }

            # Update process registry
            details = f"Проверено: {stats['checked']} | Спам: {stats['spam']} | Важные: {stats['important']}"
            process_registry.update("email_cleaner", status="IDLE", details=details)

            # Send summary to Telegram if explicitly requested or if actionable spam was detected
            if (send_tg_notification or (stats["spam"] > 0 and not self.dry_run)) and stats["checked"] > 0:
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
