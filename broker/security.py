"""
Broker Security Module.
Provides elevated security guarantees for financial operations:
1. Strictly Read-Only token verification (blocking full trading access).
2. Token masking and sanitize routines (zero secrets in logs/messages).
3. Admin chat verification (locks portfolio and financial data to admin only).
"""
import re
from typing import Any

ACCOUNT_ACCESS_LEVEL_FULL_ACCESS = 1
ACCOUNT_ACCESS_LEVEL_READ_ONLY = 2


class BrokerSecurity:
    """Security guard for financial and brokerage integrations."""

    @staticmethod
    def mask_token(token: str | None) -> str:
        """Masks sensitive token preserving only prefix and last 4 chars."""
        if not token:
            return "Не настроен"
        token = str(token).strip()
        if len(token) <= 8:
            return "t.***"
        prefix = "t." if token.startswith("t.") else token[:2]
        suffix = token[-4:]
        return f"{prefix}***...{suffix}"

    @staticmethod
    def is_valid_token_format(token: str | None) -> bool:
        """Validates Tinkoff OpenAPI token format."""
        if not token:
            return False
        token = str(token).strip()
        # Typically begins with t. and is between 30 and 150 chars
        return bool(re.match(r"^t\.[A-Za-z0-9_\-\.]{30,150}$", token))

    @staticmethod
    def check_admin_access(chat_id: int | str | None, admin_chat_id: int | str | None) -> bool:
        """Ensures that only the authorized owner can access financial data."""
        if not chat_id or not admin_chat_id:
            return False
        return str(chat_id).strip() == str(admin_chat_id).strip()

    @staticmethod
    def is_read_only(access_level: Any) -> bool:
        """
        Validates if an account or token access level is strictly Read-Only.
        Supports both string enum names and integer values from Tinkoff Invest API.
        """
        if access_level is None:
            return True
        s = str(access_level).upper()
        if "FULL" in s or access_level == ACCOUNT_ACCESS_LEVEL_FULL_ACCESS:
            return False
        return True

    @staticmethod
    def sanitize_log(text: str) -> str:
        """Removes any token-like patterns from log messages."""
        if not text:
            return ""
        return re.sub(r"t\.[A-Za-z0-9_\-\.]{15,}", "t.***[REDACTED]***", str(text))
