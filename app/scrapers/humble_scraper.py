"""
Humble Bundle store scraper.

Targets Humble's public store API endpoint which returns JSON directly,
making it significantly more reliable than HTML scraping.
Falls back to HTML parsing of the storefront if the API fails.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapeResult

logger = logging.getLogger("scraper")


class HumbleBundleScraper(BaseScraper):
    """Scraper for HumbleBundle.com.

    Primary strategy:  Humble's public store mosaic/gamekeys API.
    Fallback strategy: HTML selector parsing.
    """

    STORE_NAME = "Humble Bundle"
    BASE_URL = "https://www.humblebundle.com/store"

    # Humble's public mosaic/catalog API (pagination supported)
    _API_URL = (
        "https://www.humblebundle.com/store/api/search"
        "?sort=bestsellers&page_size=36&page=0"
        "&request=1"
    )

    selectors: dict[str, str] = {
        "title": ".entity-title, [class*='entityTitle']",
        "price": ".current-price, [class*='currentPrice']",
    }

    fallback_selectors: dict[str, list[str]] = {
        "title": [
            "span[class*='title']",
            "h2[class*='name']",
            "a[class*='entity'] span",
        ],
        "price": [
            "span[class*='price']",
            "[class*='salePrice']",
            "del + span",
        ],
    }

    def fetch_html(self, url: str) -> str:
        """Override to try Humble's JSON search API first."""
        try:
            session = self._get_session()
            response = session.get(
                self._API_URL,
                timeout=self.REQUEST_TIMEOUT,
                headers={**self._HEADERS, "Accept": "application/json"},
            )
            response.raise_for_status()
            return "__JSON__" + response.text
        except requests.RequestException as exc:
            logger.warning(
                "[HumbleBundle] API unavailable (%s), falling back to HTML.", exc
            )
            return super().fetch_html(self.BASE_URL)

    def extract_data(self, soup: BeautifulSoup) -> list[dict]:
        """Dispatch to JSON or HTML extractor based on response content.

        Args:
            soup: BeautifulSoup wrapping the raw response.

        Returns:
            List of raw product dicts.
        """
        raw_text = str(soup)

        if "__json__" in raw_text[:20].lower():
            return self._extract_from_api(raw_text)

        return self._extract_from_html(soup)

    def _extract_from_api(self, raw_text: str) -> list[dict]:
        """Parse Humble Bundle search API JSON response.

        Args:
            raw_text: JSON string with __JSON__ sentinel prefix.

        Returns:
            List of product dicts.
        """
        json_text = raw_text.replace("__JSON__", "", 1).lstrip()

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            logger.error("[HumbleBundle] API JSON parse error: %s", exc)
            return []

        results_raw = data.get("results", [])
        results = []

        for item in results_raw:
            current_price = item.get("current_price", {})
            if isinstance(current_price, dict):
                amount = current_price.get("amount", "")
                currency = current_price.get("currency", "USD")
            elif isinstance(current_price, (int, float)):
                amount = str(current_price)
                currency = "USD"
            else:
                continue

            machine_name = item.get("machine_name", "")
            url = f"https://www.humblebundle.com/store/{machine_name}" if machine_name else ""

            results.append(
                {
                    "title": item.get("human_name", "") or item.get("human_url", ""),
                    "price": str(amount),
                    "currency": currency,
                    "url": url,
                }
            )

        logger.info("[HumbleBundle] API returned %d products.", len(results))
        return results

    def _extract_from_html(self, soup: BeautifulSoup) -> list[dict]:
        """HTML fallback extraction for HumbleBundle store page.

        Args:
            soup: BeautifulSoup document.

        Returns:
            List of raw product dicts.
        """
        results = []

        card_selectors = [
            ".entity-container",
            "[class*='entityContainer']",
            "[class*='game-tile']",
            "li[class*='entity']",
        ]

        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if cards:
                break

        if not cards:
            logger.warning("[HumbleBundle] No product cards found in HTML.")
            return results

        for card in cards:
            title = self.resolve_selector(card, "title")
            price_raw = self.resolve_selector(card, "price")
            if not title:
                continue

            link_el = card.select_one("a[href]")
            url = link_el["href"] if link_el else ""
            if url and not url.startswith("http"):
                url = "https://www.humblebundle.com" + url

            results.append(
                {"title": title, "price": price_raw, "currency": "USD", "url": url}
            )

        return results

    def normalize(self, raw: dict) -> ScrapeResult | None:
        """Convert a raw HumbleBundle product dict into a ScrapeResult.

        Args:
            raw: Dict with keys: title, price, currency, url.

        Returns:
            ScrapeResult or None if the item is invalid.
        """
        title = raw.get("title", "").strip()
        price_raw = str(raw.get("price", "")).strip()
        currency = raw.get("currency", "USD")
        url = raw.get("url", "")

        # Strip HTML entities / noise from title
        title = re.sub(r"&#\d+;", " ", title).strip()

        if not title:
            return None

        price = self.parse_price(price_raw)
        if price is None or price <= 0:
            return None

        return ScrapeResult(
            title=title,
            store=self.STORE_NAME,
            price=price,
            currency=currency.upper() if currency else "USD",
            url=url,
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )
