"""
GOG store scraper.

Targets GOG's catalog sale/deals page.  Uses GOG's public search API
endpoint which returns JSON, making it more reliable than CSS scraping.
Falls back to HTML parsing if the API is unavailable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScrapeResult

logger = logging.getLogger("scraper")


class GOGScraper(BaseScraper):
    """Scraper for GOG.com.

    Primary strategy:  GOG public search API (JSON response).
    Fallback strategy: HTML parsing of the sale/deals catalog page.
    """

    STORE_NAME = "GOG"
    BASE_URL = "https://www.gog.com"

    # GOG public search API — returns JSON with price information
    _API_URL = (
        "https://catalog.gog.com/v1/catalog"
        "?limit=48&order=desc:trending"
        "&productType=in:game,pack"
        "&page=1"
    )

    # HTML fallback selectors for the catalog page
    selectors: dict[str, str] = {
        "title": "[data-test-id='product-title'], product-tile__title",
        "price": "[data-test-id='price-value'], .product-price__amount",
    }

    fallback_selectors: dict[str, list[str]] = {
        "title": [
            "[class*='productTile__title']",
            "[class*='product-title']",
            "a[class*='product']",
        ],
        "price": [
            "[class*='finalPrice']",
            "[class*='price-amount']",
            "span[class*='price']",
        ],
    }

    def fetch_html(self, url: str) -> str:
        """Override to attempt the JSON API first, then fall back to HTML.

        Returns the raw string (JSON or HTML) for further processing.
        """
        try:
            session = self._get_session()
            response = session.get(
                self._API_URL,
                timeout=self.REQUEST_TIMEOUT,
                headers={**self._HEADERS, "Accept": "application/json"},
            )
            response.raise_for_status()
            # Return a sentinel prefix so extract_data knows it's JSON
            return "__JSON__" + response.text
        except requests.RequestException as exc:
            logger.warning(
                "[GOG] API unavailable (%s), falling back to HTML scrape.", exc
            )
            return super().fetch_html(self.BASE_URL + "/games?sort=popularity")

    def extract_data(self, soup: BeautifulSoup) -> list[dict]:
        """Parse GOG catalog data.

        If the content is JSON (from the API), parse it directly.
        Otherwise, fall back to HTML BeautifulSoup extraction.
        """
        # The soup object here may be a thin wrapper around the raw string
        # because BeautifulSoup was applied to a JSON string.
        raw_text = str(soup)

        if "__json__" in raw_text[:20].lower():
            return self._extract_from_api(raw_text)

        return self._extract_from_html(soup)

    def _extract_from_api(self, raw_text: str) -> list[dict]:
        """Parse GOG catalog JSON API response.

        Args:
            raw_text: JSON string (with optional __JSON__ prefix).

        Returns:
            List of raw product dicts.
        """
        import json

        json_text = raw_text.replace("__JSON__", "", 1).lstrip()

        # BeautifulSoup may have mangled the JSON — extract via the HTML text
        # backup: try to isolate JSON within the string
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            logger.error("[GOG] JSON parse failed; no data extracted from API.")
            return []

        products = data.get("products", [])
        results = []

        for product in products:
            price_info = product.get("price", {})
            final_price = price_info.get("finalMoney", {})
            amount = final_price.get("amount", "")
            if not amount:
                continue

            results.append(
                {
                    "title": product.get("title", ""),
                    "price": str(amount),
                    "currency": final_price.get("currency", "USD"),
                    "url": f"https://www.gog.com{product.get('slug', '')}",
                }
            )

        logger.info("[GOG] API returned %d products.", len(results))
        return results

    def _extract_from_html(self, soup: BeautifulSoup) -> list[dict]:
        """Extract product data from GOG HTML catalog page.

        Args:
            soup: BeautifulSoup document of the GOG catalog page.

        Returns:
            List of raw product dicts.
        """
        results = []

        # Try multiple container selectors for resilience
        container_selectors = [
            "product-tile",
            "[class*='productTile']",
            "[class*='product-tile']",
        ]

        tiles = []
        for selector in container_selectors:
            tiles = soup.select(selector)
            if tiles:
                break

        if not tiles:
            logger.warning("[GOG] No product tiles found with any selector.")
            return results

        for tile in tiles:
            title = self.resolve_selector(tile, "title")
            price_raw = self.resolve_selector(tile, "price")

            if not title or not price_raw:
                continue

            link_el = tile.select_one("a[href]")
            url = (self.BASE_URL + link_el["href"]) if link_el else ""

            results.append(
                {"title": title, "price": price_raw, "currency": "USD", "url": url}
            )

        return results

    def normalize(self, raw: dict) -> ScrapeResult | None:
        """Convert a raw GOG product dict into a ScrapeResult.

        Args:
            raw: Dict with keys: title, price, currency, url.

        Returns:
            ScrapeResult or None if data is invalid.
        """
        title = raw.get("title", "").strip()
        price_raw = str(raw.get("price", "")).strip()
        currency = raw.get("currency", "USD")
        url = raw.get("url", "")

        if not title:
            logger.debug("[GOG] Skipping record with empty title.")
            return None

        price = self.parse_price(price_raw)
        if price is None:
            logger.debug("[GOG] Could not parse price %r for '%s'.", price_raw, title)
            return None

        if price <= 0:
            logger.debug("[GOG] Skipping free/zero-price item '%s'.", title)
            return None

        return ScrapeResult(
            title=title,
            store=self.STORE_NAME,
            price=price,
            currency=currency.upper() if currency else "USD",
            url=url,
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )
