"""
Fanatical store scraper.

Targets Fanatical's top deals / on-sale games listing.
Fanatical renders its catalog via React/JS, so we scrape the
window.__NUXT__ or window.__APP_STATE__ JSON embedded in the HTML,
with an HTML selector fallback.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapeResult

logger = logging.getLogger("scraper")


class FanaticalScraper(BaseScraper):
    """Scraper for Fanatical.com.

    Primary strategy:  Extract embedded JSON from the page's script tag.
    Fallback strategy: CSS selector parsing of product cards.
    """

    STORE_NAME = "Fanatical"
    BASE_URL = "https://www.fanatical.com/en/on-sale"

    selectors: dict[str, str] = {
        "title": "[class*='productName'], [class*='product-name']",
        "price": "[class*='finalPrice'], [class*='current-price']",
    }

    fallback_selectors: dict[str, list[str]] = {
        "title": [
            "h3[class*='title']",
            "span[class*='name']",
            "a[class*='product'] span",
        ],
        "price": [
            "span[class*='price']",
            "[data-testid='price']",
            "strong[class*='price']",
        ],
    }

    # Regex to locate embedded JSON state in Fanatical's page source
    _JSON_STATE_RE = re.compile(
        r"window\.__(?:NUXT|APP_STATE|INITIAL_STATE)__\s*=\s*(\{.+?\});",
        re.DOTALL,
    )

    def extract_data(self, soup: BeautifulSoup) -> list[dict]:
        """Attempt embedded-JSON extraction, then fall back to HTML.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            List of raw product dicts.
        """
        raw_html = str(soup)

        json_results = self._extract_from_embedded_json(raw_html)
        if json_results:
            logger.info(
                "[Fanatical] Extracted %d products from embedded JSON.",
                len(json_results),
            )
            return json_results

        logger.info("[Fanatical] Falling back to HTML selector extraction.")
        return self._extract_from_html(soup)

    def _extract_from_embedded_json(self, raw_html: str) -> list[dict]:
        """Search for and parse embedded JavaScript state objects.

        Args:
            raw_html: Raw HTML string.

        Returns:
            List of product dicts or empty list if not found / parse error.
        """
        match = self._JSON_STATE_RE.search(raw_html)
        if not match:
            return []

        try:
            state = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.debug("[Fanatical] Embedded JSON parse error: %s", exc)
            return []

        # Traverse common state shapes to find product arrays
        products = (
            self._dig(state, "data", "products")
            or self._dig(state, "products")
            or self._dig(state, "items")
            or []
        )

        results = []
        for product in products:
            if not isinstance(product, dict):
                continue
            price_block = product.get("price", {}) or {}
            price_val = (
                price_block.get("current")
                or price_block.get("final")
                or product.get("price")
                or ""
            )
            slug = product.get("slug", "") or product.get("url", "")
            results.append(
                {
                    "title": product.get("name", "") or product.get("title", ""),
                    "price": str(price_val),
                    "currency": price_block.get("currency", "USD"),
                    "url": f"https://www.fanatical.com/en/game/{slug}" if slug else "",
                }
            )
        return results

    def _extract_from_html(self, soup: BeautifulSoup) -> list[dict]:
        """CSS-based fallback extraction.

        Args:
            soup: BeautifulSoup document.

        Returns:
            List of raw product dicts.
        """
        results = []

        card_selectors = [
            "[class*='product-card']",
            "[class*='ProductCard']",
            "[class*='game-card']",
            "article[class*='product']",
        ]

        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if cards:
                break

        if not cards:
            logger.warning("[Fanatical] No product cards found via CSS selectors.")
            return results

        for card in cards:
            title = self.resolve_selector(card, "title")
            price_raw = self.resolve_selector(card, "price")
            if not title:
                continue

            link_el = card.select_one("a[href]")
            url = link_el["href"] if link_el else ""
            if url and not url.startswith("http"):
                url = "https://www.fanatical.com" + url

            results.append(
                {"title": title, "price": price_raw, "currency": "USD", "url": url}
            )

        return results

    def normalize(self, raw: dict) -> ScrapeResult | None:
        """Convert a raw Fanatical product dict into a ScrapeResult.

        Args:
            raw: Dict with keys: title, price, currency, url.

        Returns:
            ScrapeResult or None if the item is invalid.
        """
        title = raw.get("title", "").strip()
        price_raw = str(raw.get("price", "")).strip()
        currency = raw.get("currency", "USD")
        url = raw.get("url", "")

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

    @staticmethod
    def _dig(data: dict, *keys: str):
        """Safely traverse nested dicts by a sequence of keys.

        Returns:
            The nested value or None if any key is missing.
        """
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current
