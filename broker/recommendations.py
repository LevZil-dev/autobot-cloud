"""
AI Investment Proposals & Ticker Deep-Dive Engine.
Uses Gemini 3.8 Flash to evaluate companies, financial reports, dividend potential,
and generate high-conviction investment ideas for the user.
"""
import os
import json
from typing import Any

from google import genai
from google.genai import types

from .news_analyzer import NewsAnalyzer


# Blue chips knowledge base for quick lookups & context
KEY_COMPANIES = {
    "SBER": {"name": "Сбербанк", "sector": "Банки и финансы", "dividend_yield": "11-13%", "pe": "3.8"},
    "T": {"name": "Т-Банк (ТКС Холдинг)", "sector": "Финтех", "dividend_yield": "Квартальные дивиденды", "pe": "6.2"},
    "LKOH": {"name": "Лукойл", "sector": "Нефть и газ", "dividend_yield": "14-16%", "pe": "4.1"},
    "YDEX": {"name": "Яндекс", "sector": "IT и Технологии", "dividend_yield": "Реинвестирование + первый дивиденд", "pe": "14.5"},
    "OZON": {"name": "Озон", "sector": "Электронная коммерция", "dividend_yield": "Ростовая история", "pe": "Рост выручки >35%"},
    "NVTK": {"name": "Новатэк", "sector": "СПГ и Газ", "dividend_yield": "7-9%", "pe": "5.5"},
    "PLZL": {"name": "Полюс Золото", "sector": "Металлургия / Золото", "dividend_yield": "Защитный актив", "pe": "6.0"},
    "GAZP": {"name": "Газпром", "sector": "Газ", "dividend_yield": "Высокая долговая нагрузка", "pe": "3.0"},
}


class RecommendationEngine:
    """Generates market proposals and detailed ticker analysis using Gemini AI."""

    def __init__(self, api_key: str | None = None, news_analyzer: NewsAnalyzer | None = None):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.news_analyzer = news_analyzer or NewsAnalyzer(api_key=self.api_key)
        self.ai_client: genai.Client | None = None
        if self.api_key:
            try:
                self.ai_client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[RecommendationEngine] AI client init error: {e}")

    def generate_top_proposals(self, limit: int = 4) -> list[dict[str, Any]]:
        """
        Synthesizes live news, corporate drivers, and market conditions
        into top high-conviction investment ideas for the user.
        """
        news_items = self.news_analyzer.fetch_market_news(limit=6)
        news_summary = "\n".join([f"- {n['title']}: {n['summary'][:150]}" for n in news_items])

        if not self.ai_client:
            return self._fallback_top_proposals()

        prompt = (
            "Ты — профессиональный инвестиционный стратег и портфельный управляющий по рынку ценных бумаг РФ (Московская биржа, Т-Инвестиции).\n"
            "На основе свежего новостного фона и макроэкономических факторов выбери ТОП самых привлекательных инвестиционных предложений.\n\n"
            f"Свежий новостной фон:\n{news_summary}\n\n"
            "Базовый пул компаний для выбора:\n"
            "- Сбербанк (SBER)\n- Т-Банк / ТКС Холдинг (T)\n- Лукойл (LKOH)\n- Яндекс (YDEX)\n- Озон (OZON)\n- Новатэк (NVTK)\n- Полюс (PLZL)\n\n"
            "Сформируй ровно 3-4 лучшие идеи СТРОГО в формате JSON (массив объектов):\n"
            "[\n"
            "  {\n"
            '    "ticker": "SBER",\n'
            '    "name": "Сбербанк",\n'
            '    "signal": "BUY" | "ACCUMULATE" | "HOLD" | "TAKE_PROFIT",\n'
            '    "upside": "Ожидаемый потенциал, например +15-20%",\n'
            '    "horizon": "Горизонт, например Среднесрок (1-3 мес) или Дивиденды",\n'
            '    "rationale": "Убедительное обоснование на русском (2 предложения): почему бумага привлекательна, драйверы роста",\n'
            '    "risk": "LOW" | "MEDIUM" | "HIGH"\n'
            "  }\n"
            "]"
        )

        try:
            response = self.ai_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.strip("`").replace("json\n", "", 1)
            ideas = json.loads(raw)
            if isinstance(ideas, list) and len(ideas) > 0:
                return ideas[:limit]
            return self._fallback_top_proposals()
        except Exception as e:
            print(f"[RecommendationEngine Error]: {e}")
            return self._fallback_top_proposals()

    def analyze_ticker(self, ticker: str) -> dict[str, Any]:
        """Provides a deep-dive AI investment analysis for any given ticker."""
        clean_ticker = ticker.upper().strip().replace("$", "")
        info = KEY_COMPANIES.get(clean_ticker, {
            "name": clean_ticker,
            "sector": "Российский фондовый рынок",
            "dividend_yield": "Политика компании",
            "pe": "Рыночный мультипликатор",
        })

        if not self.ai_client:
            return {
                "ticker": clean_ticker,
                "name": info["name"],
                "sector": info["sector"],
                "signal": "BUY" if clean_ticker in ("SBER", "T", "LKOH", "YDEX") else "HOLD",
                "upside": "+12-18%",
                "horizon": "Среднесрок (3-6 мес)",
                "summary": f"Компания {info['name']} сохраняет стабильные финансовые метрики и высокий спрос со стороны инвесторов.",
                "drivers": ["Высокая операционная рентабельность", "Дивидендная доходность выше рынка"],
                "risks": ["Общерыночная волатильность", "Уровень ключевой ставки ЦБ"],
            }

        prompt = (
            f"Проведи глубокий инвестиционный анализ акции {clean_ticker} ({info['name']}) для инвестора Т-Инвестиций.\n"
            f"Сектор: {info['sector']}\n\n"
            "Верни ответ СТРОГО в формате JSON:\n"
            "{\n"
            f'  "ticker": "{clean_ticker}",\n'
            f'  "name": "{info["name"]}",\n'
            f'  "sector": "{info["sector"]}",\n'
            '  "signal": "BUY" | "ACCUMULATE" | "HOLD" | "TAKE_PROFIT",\n'
            '  "upside": "например: +15-20%",\n'
            '  "horizon": "например: 3-6 месяцев",\n'
            '  "summary": "Краткий вывод на 2-3 предложения на русском",\n'
            '  "drivers": ["Ключевой драйвер 1", "Ключевой драйвер 2"],\n'
            '  "risks": ["Главный риск 1", "Главный риск 2"]\n'
            "}"
        )

        try:
            response = self.ai_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.strip("`").replace("json\n", "", 1)
            return json.loads(raw)
        except Exception as e:
            print(f"[Analyze Ticker Error]: {e}")
            return {
                "ticker": clean_ticker,
                "name": info["name"],
                "sector": info["sector"],
                "signal": "BUY",
                "upside": "+10-15%",
                "horizon": "3-6 месяцев",
                "summary": f"{info['name']} фундаментально устойчива. Резервный режим оценки.",
                "drivers": ["Стабильный денежный поток", "Лидерство в секторе"],
                "risks": ["Макроэкономическая конъюнктура"],
            }

    def _fallback_top_proposals(self) -> list[dict[str, Any]]:
        """Reliable fallback investment proposals."""
        return [
            {
                "ticker": "SBER",
                "name": "Сбербанк",
                "signal": "BUY",
                "upside": "+15-20%",
                "horizon": "Дивиденды / 2-4 мес",
                "rationale": "Рекордная чистая прибыль и прогнозируемая дивидендная доходность ~12-13% делают акцию флагманом рынка.",
                "risk": "LOW",
            },
            {
                "ticker": "T",
                "name": "Т-Банк (ТКС Холдинг)",
                "signal": "BUY",
                "upside": "+20-25%",
                "horizon": "Среднесрок (3-6 мес)",
                "rationale": "Интеграция с Росбанком, ускорение темпов роста кредитного и комиссионного портфеля и регулярные выплаты дивидендов.",
                "risk": "MEDIUM",
            },
            {
                "ticker": "LKOH",
                "name": "Лукойл",
                "signal": "BUY",
                "upside": "+12-16%",
                "horizon": "Дивиденды / 2-3 мес",
                "rationale": "Крупнейшая кубышка ликвидности в секторе и стабильный двузначный дивидендный поток с защитой от девальвации рубля.",
                "risk": "LOW",
            },
            {
                "ticker": "YDEX",
                "name": "Яндекс",
                "signal": "ACCUMULATE",
                "upside": "+18-24%",
                "horizon": "Долгосрок (6-12 мес)",
                "rationale": "Рост выручки выше 35% в год, доминирование в поиске, рекламе и такси, завершение реструктуризации бизнеса.",
                "risk": "MEDIUM",
            },
        ]
