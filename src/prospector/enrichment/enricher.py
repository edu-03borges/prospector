"""Enriquecimento de leads: e-mail, redes sociais e estimativa por domínio."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from prospector.config.settings import cfg, get_settings
from prospector.models.lead import Lead, SocialMedia

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_SOCIAL_PATTERNS = {
    "instagram": re.compile(r"instagram\.com/([A-Za-z0-9_.]+)"),
    "facebook": re.compile(r"facebook\.com/([A-Za-z0-9_.]+)"),
    "youtube": re.compile(r"youtube\.com/(?:channel/|@|c/)([A-Za-z0-9_.\-]+)"),
    "tiktok": re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)"),
    "linkedin": re.compile(r"linkedin\.com/(?:company|in)/([A-Za-z0-9_.\-]+)"),
}

_COMMON_EMAIL_PREFIXES = ["contato", "contact", "info", "studio", "estudio", "comercial", "ola"]


class LeadEnricher:
    """Enriquece leads com dados de e-mail e redes sociais."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "LeadEnricher":
        self._client = httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def enrich(self, lead: Lead) -> Lead:
        """Enriquece um lead com todos os dados possíveis."""
        tasks = []

        # 1. Busca pelo site do estúdio
        if lead.website:
            tasks.append(self._scrape_website(lead))

        # 2. Hunter.io para e-mail
        if self._settings.has_hunter and not lead.email:
            tasks.append(self._hunt_email(lead))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # 3. Estima e-mail se ainda vazio e tiver site
        if not lead.email and lead.website:
            lead.email, lead.email_confidence, lead.email_is_estimated = (
                self._estimate_email(lead.website)
            )

        return lead

    async def _scrape_website(self, lead: Lead) -> None:
        """Extrai e-mails e redes sociais direto do site."""
        if not self._client or not lead.website:
            return
        try:
            response = await self._client.get(lead.website, timeout=15)
            if response.status_code != 200:
                return
            html = response.text
            soup = BeautifulSoup(html, "lxml")

            # E-mails diretos no HTML
            emails = _EMAIL_PATTERN.findall(html)
            for email in emails:
                if self._is_valid_email(email):
                    lead.email = email
                    lead.email_confidence = 0.95
                    break

            # Redes sociais
            social = lead.social or SocialMedia()
            all_links = " ".join([a.get("href", "") for a in soup.find_all("a", href=True)])
            all_links += html

            for platform, pattern in _SOCIAL_PATTERNS.items():
                match = pattern.search(all_links)
                if match and not getattr(social, platform):
                    handle = match.group(1).rstrip("/")
                    setattr(social, platform, f"https://{platform}.com/{handle}")

            lead.social = social
        except Exception as exc:
            logger.debug(f"[Enricher] Falha ao scrape site {lead.website}: {exc}")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _hunt_email(self, lead: Lead) -> None:
        """Busca e-mail via Hunter.io Domain Search."""
        if not self._client or not lead.website:
            return
        domain = urlparse(lead.website).netloc.replace("www.", "")
        if not domain:
            return
        try:
            resp = await self._client.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain,
                    "api_key": self._settings.hunter_api_key,
                    "limit": 3,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            emails = data.get("emails", [])
            if emails:
                best = sorted(emails, key=lambda e: e.get("confidence", 0), reverse=True)[0]
                lead.email = best["value"]
                lead.email_confidence = best.get("confidence", 50) / 100
                lead.email_is_estimated = False
                logger.debug(f"[Hunter] E-mail encontrado: {lead.email}")
        except Exception as exc:
            logger.debug(f"[Hunter] Falha para {lead.website}: {exc}")

    def _estimate_email(self, website: str) -> tuple[str, float, bool]:
        """Estima o e-mail mais provável com base no domínio."""
        domain = urlparse(website).netloc.replace("www.", "")
        if not domain:
            return ("", 0.0, True)
        email = f"contato@{domain}"
        return (email, 0.30, True)

    def _is_valid_email(self, email: str) -> bool:
        """Filtra e-mails genéricos e inválidos."""
        blacklist = {
            "example.com", "test.com", "email.com", "sentry.io",
            "w3.org", "schema.org", "google.com",
        }
        if not email or "@" not in email:
            return False
        domain = email.split("@")[1].lower()
        return domain not in blacklist and len(email) < 100
