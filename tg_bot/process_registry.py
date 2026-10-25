"""
Process Registry for AutoBot & Cloud Agents.
Maintains live statuses, heartbeats, and statistics of all background tasks.
"""
import time
from typing import Any
import psutil


class ProcessInfo:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.status = "IDLE"  # IDLE, RUNNING, ERROR, STOPPED
        self.last_update = time.time()
        self.stats: dict[str, Any] = {}
        self.error_message: str | None = None

    def update(self, status: str, details: str = "", **kwargs):
        self.status = status
        self.last_update = time.time()
        if details:
            self.stats["details"] = details
        self.stats.update(kwargs)


class ProcessRegistry:
    def __init__(self):
        self._processes: dict[str, ProcessInfo] = {}
        # Pre-register default core agents
        self.register("autobot_local", "AutoBot — Локальный оператор ПК")
        self.register("email_cleaner", "Cloud / Local Email Cleaner")
        self.register("system_monitor", "Системный монитор ресурсов")

    def register(self, process_id: str, description: str = "") -> ProcessInfo:
        if process_id not in self._processes:
            self._processes[process_id] = ProcessInfo(process_id, description)
        return self._processes[process_id]

    def update(self, process_id: str, status: str, details: str = "", **kwargs):
        if process_id not in self._processes:
            self.register(process_id)
        self._processes[process_id].update(status, details, **kwargs)

    def get_all(self) -> dict[str, ProcessInfo]:
        return self._processes

    def get_formatted_status(self) -> str:
        """Returns a formatted Markdown dashboard for Telegram."""
        now = time.time()
        lines = ["📊 *Статус автономных процессов AutoBot:*\n"]

        status_icons = {
            "IDLE": "⚪ Готов",
            "RUNNING": "🟢 Работает",
            "ACTIVE": "🟢 Активен",
            "ERROR": "🔴 Ошибка",
            "STOPPED": "⚫ Остановлен",
        }

        for pid, p in self._processes.items():
            age = int(now - p.last_update)
            icon = status_icons.get(p.status.upper(), f"🟡 {p.status}")
            lines.append(f"*{p.description or pid}*")
            lines.append(f"  └ Состояние: {icon}")
            if p.stats.get("details"):
                lines.append(f"  └ Инфо: _{p.stats['details']}_")
            lines.append(f"  └ Обновлено: {age} сек назад\n")

        # System hardware stats
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            lines.append(f"🖥 *Ресурсы хоста (ПК):*")
            lines.append(f"  └ CPU: `{cpu}%` | RAM: `{mem}%`")
        except Exception:
            pass

        return "\n".join(lines)


# Global registry singleton
process_registry = ProcessRegistry()
