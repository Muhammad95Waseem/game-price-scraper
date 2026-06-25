from __future__ import annotations
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
import requests
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.db import db
from app.models import Game, Price, Store

logger = logging.getLogger("scraper")

class ScrapeResult:
    __slots__ = ("title", "store", "price", "currency", "url", "scraped_at")

    def __init__(self, *, title: str, store: str, price: float, currency: str = "USD", url: str = "", scraped_at: str = "") -> None:
        self.title = title.strip()
        self.store = store.strip()
        self.price = float(price)
        self.currency = currency.upper()
        self.url = url.strip()
        self.scraped_at = scraped_at or datetime.now(timezone.utc).isoformat()

class BaseScraper(ABC):
    STORE_NAME: str = ""
    BASE_URL: str = ""
    selectors: dict[str, str] = {"title": "", "price": ""}
    fallback_selectors: dict[str, list[str]] = {"title": [], "price": []}
    _session: requests.Session | None = None
    REQUEST_TIMEOUT: int = 15
    MAX_RETRIES: int = 3
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def run(self) -> list[ScrapeResult]:
        logger.info("[%s] Starting scrape run.", self.STORE_NAME)
        results: list[ScrapeResult] = []
        try:
            html = self.fetch_html(self.BASE_URL)
            if not html:
                return results
            soup = BeautifulSoup(html, "lxml")
            raw_items = self.extract_data(soup)
            for raw in raw_items:
                result = self._safe_normalize(raw)
                if result is not None:
                    self.save(result)
                    results.append(result)
        except Exception as exc:
            logger.exception("[%s] Unhandled exception during scrape run: %s", self.STORE_NAME, exc)
        return results

    def fetch_html(self, url: str) -> str:
        session = self._get_session()
        try:
            return self._fetch_with_retry(session, url)
        except Exception as exc:
            logger.error("[%s] fetch_html failed for %s: %s", self.STORE_NAME, url, exc)
            return ""

    @retry(retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _fetch_with_retry(self, session: requests.Session, url: str) -> str:
        response = session.get(url, timeout=self.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.text

    @abstractmethod
    def extract_data(self, soup: BeautifulSoup) -> list[dict]: pass
    @abstractmethod
    def normalize(self, raw: dict) -> ScrapeResult | None: pass

    def save(self, result: ScrapeResult) -> None:
        try:
            store = self._get_or_create_store(result.store)
            game = self._get_or_create_game(result.title)
            price = Price(
                game_id=game.id, store_id=store.id, price=result.price,
                currency=result.currency, url=result.url,
                scraped_at=datetime.fromisoformat(result.scraped_at),
            )
            db.session.add(price)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("[%s] Database error saving '%s': %s", self.STORE_NAME, result.title, exc)

    def resolve_selector(self, soup: BeautifulSoup, field: str, *, attribute: str | None = None) -> str:
        candidates = [self.selectors.get(field, "")] + self.fallback_selectors.get(field, [])
        for selector in candidates:
            if not selector: continue
            try:
                element = soup.select_one(selector)
                if element is not None:
                    value = element.get(attribute, "").strip() if attribute else element.get_text(strip=True)
                    if value: return value
            except Exception: pass
        return self._regex_fallback(str(soup), field)

    @staticmethod
    def _regex_fallback(html: str, field: str) -> str:
        if field == "price":
            match = re.search(r"[\$€£]?\s*(\d{1,4}[.,]\d{2})", html)
            if match: return match.group(1).replace(",", ".")
        return ""

    @staticmethod
    def parse_price(raw_price: str) -> float | None:
        cleaned = re.sub(r"[^\d.,]", "", raw_price.strip())
        if re.match(r"^\d{1,3},\d{2}$", cleaned): cleaned = cleaned.replace(",", ".")
        else: cleaned = cleaned.replace(",", "")
        try: return float(cleaned)
        except ValueError: return None

    def _safe_normalize(self, raw: dict) -> ScrapeResult | None:
        try: return self.normalize(raw)
        except Exception: return None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self._HEADERS)
        return self._session

    @staticmethod
    def _get_or_create_game(title: str) -> Game:
        game = db.session.execute(db.select(Game).filter_by(title=title)).scalar_one_or_none()
        if game is None:
            game = Game(title=title)
            db.session.add(game)
            db.session.flush()
        return game

    @staticmethod
    def _get_or_create_store(name: str) -> Store:
        store = db.session.execute(db.select(Store).filter_by(name=name)).scalar_one_or_none()
        if store is None:
            store = Store(name=name)
            db.session.add(store)
            db.session.flush()
        return store