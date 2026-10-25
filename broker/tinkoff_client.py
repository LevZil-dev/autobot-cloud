"""
Tinkoff Investments (T-Invest) Read-Only REST Client.
Connects to official Tinkoff Invest API v2 via REST endpoints.
Guarantees 100% safety: ONLY read-only portfolio & market data methods are implemented.
"""
import os
import json
import urllib.request
import urllib.error
from typing import Any

from .security import BrokerSecurity

BASE_URL = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.v1."


def _parse_money(val: dict[str, Any] | None) -> float:
    """Helper to convert Tinkoff MoneyValue or Quotation to float."""
    if not val or not isinstance(val, dict):
        return 0.0
    units = int(val.get("units", 0))
    nano = int(val.get("nano", 0))
    return units + (nano / 1_000_000_000.0)


class TinkoffClient:
    """Read-Only client for T-Invest (Tinkoff Investments) portfolio analysis."""

    def __init__(self, token: str | None = None, account_id: str | None = None):
        self.token = (token or os.getenv("TINKOFF_INVEST_TOKEN", "")).strip()
        self.account_id = (account_id or os.getenv("TINKOFF_ACCOUNT_ID", "")).strip()
        self.strict_read_only = True

    def is_configured(self) -> bool:
        """Checks if a valid token is present."""
        return bool(self.token and len(self.token) > 15)

    def set_token(self, token: str) -> dict[str, Any]:
        """
        Validates, tests and sets a new API token.
        Enforces read-only verification.
        """
        clean_token = token.strip()
        if not BrokerSecurity.is_valid_token_format(clean_token):
            return {
                "success": False,
                "error": "Неверный формат токена! Токен должен начинаться с 't.' и содержать только латинские символы/цифры."
            }

        # Test request to GetAccounts
        test_client = TinkoffClient(token=clean_token)
        accounts_res = test_client.get_accounts()
        if not accounts_res.get("success"):
            return {
                "success": False,
                "error": f"Ошибка авторизации в Т-Инвестициях: {accounts_res.get('error')}"
            }

        accounts = accounts_res.get("accounts", [])
        if not accounts:
            return {
                "success": False,
                "error": "Успешная авторизация, но в профиле не найдено брокерских счетов."
            }

        # Check accessLevel for elevated security
        first_acc = accounts[0]
        access_lvl = first_acc.get("accessLevel")
        if self.strict_read_only and not BrokerSecurity.is_read_only(access_lvl):
            return {
                "success": False,
                "error": (
                    "⚠️ ПОВЫШЕННЫЙ УРОВЕНЬ БЕЗОПАСНОСТИ: Данный токен имеет полный доступ к совершению сделок! "
                    "В целях 100% защиты ваших средств перевыпустите токен в приложении Т-Инвестиций с галочкой 'Только чтение' (Read-Only)."
                )
            }

        # Token is validated
        self.token = clean_token
        self.account_id = first_acc.get("id", "")
        return {
            "success": True,
            "masked_token": BrokerSecurity.mask_token(self.token),
            "account_name": first_acc.get("name", "Основной счет"),
            "account_id": self.account_id,
            "total_accounts": len(accounts),
        }

    def _api_post(self, service_method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Executes an authorized POST request to Tinkoff REST API."""
        if not self.token:
            return {"success": False, "error": "Токен Т-Инвестиций не настроен"}

        url = f"{BASE_URL}{service_method}"
        body = json.dumps(payload or {}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "x-app-name": "AutoBot.InvestmentHub",
        }

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "data": data}
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read().decode("utf-8"))
                msg = err_data.get("message", f"HTTP {e.code}")
            except Exception:
                msg = f"HTTP {e.code}"
            return {"success": False, "error": msg, "code": e.code}
        except Exception as ex:
            return {"success": False, "error": str(ex)}

    def get_accounts(self) -> dict[str, Any]:
        """Fetches list of investment accounts (Read-Only)."""
        res = self._api_post("UsersService/GetAccounts")
        if not res.get("success"):
            return res
        raw_accounts = res.get("data", {}).get("accounts", [])
        clean_accounts = []
        for a in raw_accounts:
            clean_accounts.append({
                "id": a.get("id"),
                "type": a.get("type"),
                "name": a.get("name", "Брокерский счет"),
                "status": a.get("status"),
                "accessLevel": a.get("accessLevel"),
            })
        return {"success": True, "accounts": clean_accounts}

    def get_portfolio(self, account_id: str | None = None) -> dict[str, Any]:
        """
        Fetches current portfolio: total amount, expected yield, and positions.
        Falls back to Demo Watchlist if token is not configured.
        """
        if not self.is_configured():
            return self.get_demo_portfolio()

        target_acc = account_id or self.account_id
        if not target_acc:
            accs = self.get_accounts()
            if accs.get("success") and accs.get("accounts"):
                target_acc = accs["accounts"][0]["id"]
                self.account_id = target_acc
            else:
                return self.get_demo_portfolio()

        payload = {"accountId": target_acc, "currency": "RUB"}
        res = self._api_post("OperationsService/GetPortfolio", payload)
        if not res.get("success"):
            return {
                "success": False,
                "is_demo": False,
                "error": res.get("error"),
                "masked_token": BrokerSecurity.mask_token(self.token),
            }

        data = res.get("data", {})
        total_amount_shares = _parse_money(data.get("totalAmountShares"))
        total_amount_bonds = _parse_money(data.get("totalAmountBonds"))
        total_amount_etf = _parse_money(data.get("totalAmountEtf"))
        total_amount_currencies = _parse_money(data.get("totalAmountCurrencies"))
        total_portfolio = total_amount_shares + total_amount_bonds + total_amount_etf + total_amount_currencies
        expected_yield = _parse_money(data.get("expectedYield"))

        positions = []
        for pos in data.get("positions", []):
            quantity = _parse_money(pos.get("quantity"))
            avg_price = _parse_money(pos.get("averagePositionPrice"))
            current_price = _parse_money(pos.get("currentPrice"))
            pos_yield = _parse_money(pos.get("expectedYield"))
            figi = pos.get("figi", "")
            instrument_type = pos.get("instrumentType", "share")

            positions.append({
                "figi": figi,
                "type": instrument_type,
                "quantity": quantity,
                "average_price": avg_price,
                "current_price": current_price,
                "yield": pos_yield,
            })

        return {
            "success": True,
            "is_demo": False,
            "account_id": target_acc,
            "masked_token": BrokerSecurity.mask_token(self.token),
            "total_portfolio_rub": round(total_portfolio, 2),
            "expected_yield_rub": round(expected_yield, 2),
            "shares_rub": round(total_amount_shares, 2),
            "bonds_rub": round(total_amount_bonds, 2),
            "currencies_rub": round(total_amount_currencies, 2),
            "positions_count": len(positions),
            "positions": positions,
        }

    def get_demo_portfolio(self) -> dict[str, Any]:
        """
        Returns realistic sample portfolio of top MOEX blue chips.
        Allows testing all AI and UI features before token is entered.
        """
        demo_positions = [
            {
                "ticker": "SBER",
                "name": "Сбербанк",
                "quantity": 250,
                "average_price": 272.50,
                "current_price": 298.40,
                "yield": +6475.0,
                "yield_pct": +9.5,
                "sector": "Финансы",
            },
            {
                "ticker": "YDEX",
                "name": "Яндекс",
                "quantity": 20,
                "average_price": 3850.0,
                "current_price": 4210.0,
                "yield": +7200.0,
                "yield_pct": +9.3,
                "sector": "IT и Технологии",
            },
            {
                "ticker": "LKOH",
                "name": "Лукойл",
                "quantity": 12,
                "average_price": 6850.0,
                "current_price": 7090.0,
                "yield": +2880.0,
                "yield_pct": +3.5,
                "sector": "Нефть и Газ",
            },
            {
                "ticker": "T",
                "name": "Т-Банк (ТКС)",
                "quantity": 30,
                "average_price": 2490.0,
                "current_price": 2680.0,
                "yield": +5700.0,
                "yield_pct": +7.6,
                "sector": "Финтех",
            },
            {
                "ticker": "OZON",
                "name": "Озон",
                "quantity": 25,
                "average_price": 3600.0,
                "current_price": 3840.0,
                "yield": +6000.0,
                "yield_pct": +6.7,
                "sector": "Электронная коммерция",
            },
        ]

        total_value = sum(p["quantity"] * p["current_price"] for p in demo_positions)
        total_profit = sum(p["yield"] for p in demo_positions)

        return {
            "success": True,
            "is_demo": True,
            "account_name": "Демо-портфель (Голубые фишки РФ)",
            "masked_token": "Токен не подключен",
            "total_portfolio_rub": round(total_value, 2),
            "expected_yield_rub": round(total_profit, 2),
            "yield_pct": round((total_profit / (total_value - total_profit)) * 100, 2),
            "positions_count": len(demo_positions),
            "positions": demo_positions,
        }
