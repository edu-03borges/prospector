"""Scraper ético via Playwright — busca no Google Maps quando sem API key."""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from loguru import logger
from playwright.async_api import Browser, Page, async_playwright

from prospector.config.settings import cfg
from prospector.models.lead import Lead, SearchQuery, SocialMedia, StudioType

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

_PHONE_PATTERN = re.compile(r"(\(?\d{2}\)?\s?[\d\s\-]{8,11})")
_RATING_PATTERN = re.compile(r"(\d[.,]\d)")
_REVIEWS_PATTERN = re.compile(r"\((\d[\d.]+)\)")


def _clean_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) >= 8 else None


class MapsScraper:
    """
    Realiza scraping ético do Google Maps abrindo um navegador Chromium.
    Respeita delays e não bypassa CAPTCHAs.
    """

    delay: float = cfg("scraping.request_delay_seconds", 2.5)
    max_results: int = cfg("scraping.max_results_per_query", 60)

    async def search(self, query: SearchQuery) -> AsyncIterator[Lead]:
        """Ponto de entrada principal: itera sobre todos os leads."""
        keywords = query.keywords or [f"estúdio {query.city} {query.state}"]

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(
                user_agent=_USER_AGENTS[0],
                viewport={"width": 1280, "height": 800},
                locale="pt-BR",
            )
            try:
                for keyword in keywords:
                    logger.info(f"[Scraper] Buscando: '{keyword}'")
                    async for lead in self._scrape_maps(context, keyword, query):
                        yield lead
            finally:
                await browser.close()

    async def _scrape_maps(self, context, keyword: str, query: SearchQuery) -> AsyncIterator[Lead]:
        search_term = f"{keyword} {query.city} {query.state} Brasil"
        url = f"https://www.google.com/maps/search/{quote_plus(search_term)}"

        page = await context.new_page()
        seen_names: set[str] = set()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(3)

            # Rejeita cookies se aparecer
            try:
                btn = page.locator("button:has-text('Rejeitar tudo')")
                if await btn.count() > 0:
                    await btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # Rola a lista de resultados até o fim
            sidebar = page.locator("div[role='feed']")
            fetched = 0
            prev_count = 0
            stall_count = 0

            while fetched < query.max_results and stall_count < 5:
                items = await page.locator("div[role='feed'] a[href*='/maps/place/']").all()
                current_count = len(items)

                if current_count == prev_count:
                    stall_count += 1
                else:
                    stall_count = 0
                    prev_count = current_count

                # Extrai itens visíveis
                for item in items[fetched:]:
                    try:
                        lead = await self._extract_card(context, item, query)
                        if lead and lead.name not in seen_names:
                            seen_names.add(lead.name)
                            fetched += 1
                            yield lead
                    except Exception as exc:
                        logger.debug(f"Erro ao extrair card: {exc}")

                if fetched >= query.max_results:
                    break

                # Scroll down na sidebar
                try:
                    await sidebar.evaluate("el => el.scrollTop += 600")
                except Exception:
                    await page.keyboard.press("End")
                await asyncio.sleep(self.delay)

        except Exception as exc:
            logger.error(f"[Scraper] Falha ao scrapear Maps: {exc}")
        finally:
            await page.close()

    async def _extract_card(self, context, item, query: SearchQuery) -> Lead | None:
        """Extrai dados de um card de resultado do Maps e busca telefone em aba separada."""
        try:
            href = await item.get_attribute("href") or ""
            aria_label = await item.get_attribute("aria-label") or ""
            inner_text = await item.inner_text()

            name = aria_label.strip() or inner_text.split("\n")[0].strip()
            if not name:
                return None

            # Tenta abrir a página do lugar para pegar telefone e website com precisão
            phone = None
            website = None
            if href:
                detail_page = await context.new_page()
                try:
                    await detail_page.goto(href, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2.5) # Aguarda render pesado da sidebar do maps
                    
                    # Seletor moderno de telefone via aria-label ou âncora tel:
                    phone_loc = detail_page.locator('button[aria-label^="Telefone:"]')
                    if await phone_loc.count() > 0:
                        raw_aria = await phone_loc.first.get_attribute("aria-label")
                        phone = raw_aria.replace("Telefone:", "").strip() if raw_aria else None
                    else:
                        phone_tel_loc = detail_page.locator('a[href^="tel:"]')
                        if await phone_tel_loc.count() > 0:
                            phone = await phone_tel_loc.first.get_attribute("href")
                            
                    # Fallback infalível via Regex no texto puro da tela (caso o DOM mude ou esteja oculto)
                    if not phone:
                        try:
                            body_text = await detail_page.evaluate("document.body.innerText")
                            phone_match = _PHONE_PATTERN.search(body_text)
                            if phone_match:
                                phone = phone_match.group(1)
                        except Exception:
                            pass
                    
                    # Seletor robusto de website
                    web_loc = detail_page.locator('a[aria-label^="Website:"]')
                    if await web_loc.count() > 0:
                        website = await web_loc.first.get_attribute("href")
                    else:
                        web_loc_alt = detail_page.locator('a[data-item-id="authority"]')
                        if await web_loc_alt.count() > 0:
                            website = await web_loc_alt.first.get_attribute("href")
                        
                except Exception as exc:
                    logger.debug(f"Timeout ao abrir detalhes do {name}: {exc}")
                finally:
                    await detail_page.close()
            
            # Matchers de avaliações no texto inicial
            rating_match = _RATING_PATTERN.search(inner_text)
            reviews_match = _REVIEWS_PATTERN.search(inner_text)

            rating = float(rating_match.group(1).replace(",", ".")) if rating_match else None
            reviews = None
            if reviews_match:
                reviews_str = reviews_match.group(1).replace(".", "")
                try:
                    reviews = int(reviews_str)
                except ValueError:
                    pass

            # Extrai cidade do resultado
            lines = [l.strip() for l in inner_text.split("\n") if l.strip()]
            address = lines[2] if len(lines) > 2 else None

            return Lead(
                source="maps_scraper",
                name=name,
                phone=_clean_phone(phone),
                website=website,
                address=address,
                city=query.city,
                state=query.state,
                rating=rating,
                review_count=reviews,
                google_maps_url=href,
                social=SocialMedia(),
                studio_type=StudioType.DESCONHECIDO,
            )
        except Exception as exc:
            logger.debug(f"Erro no card: {exc}")
            return None

