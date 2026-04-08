"""Integração com a Google Places API (New) — busca de estúdios."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from prospector.config.settings import get_settings
from prospector.models.lead import Lead, SearchQuery, SocialMedia, StudioType


_PLACES_TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
_PLACE_DETAILS = "https://places.googleapis.com/v1/places/{place_id}"

_STUDIO_TYPE_KEYWORDS: dict[str, list[str]] = {
    "gravacao": ["studio fitness", "recording studio", "estudio gravacao"],
    "musica": ["studio de música", "produção musical", "mixing mastering"],
    "foto": ["estúdio fotográfico", "studio foto", "estudio fotografico"],
    "video": ["estúdio audiovisual", "studio de vídeo", "produtora de vídeo"],
    "podcast": ["estúdio de podcast", "studio podcast"],
    "criativo": ["estúdio criativo", "coworking criativo", "studio criativo"],
}


def _guess_studio_type(name: str, types: list[str]) -> StudioType:
    combined = (name + " ".join(types)).lower()
    if any(k in combined for k in ["gravaç", "record", "musica", "mixing"]):
        return StudioType.GRAVACAO
    if any(k in combined for k in ["foto", "photo"]):
        return StudioType.FOTO
    if any(k in combined for k in ["video", "vídeo", "audiovisual", "cinema"]):
        return StudioType.VIDEO
    if any(k in combined for k in ["podcast"]):
        return StudioType.PODCAST
    if any(k in combined for k in ["criativ", "creative", "coworking"]):
        return StudioType.CRIATIVO
    return StudioType.DESCONHECIDO


class GooglePlacesSearcher:
    """Busca estúdios via Google Places API (New) de forma assíncrona."""

    def __init__(self) -> None:
        self._settings = get_settings()
        if not self._settings.has_google_api:
            raise RuntimeError(
                "GOOGLE_PLACES_API_KEY não configurada. "
                "Adicione no .env ou use o scraper como fallback."
            )

    async def search(self, query: SearchQuery) -> AsyncIterator[Lead]:
        """Itera sobre todos os leads encontrados para a query."""
        keywords = query.keywords or [
            f"estúdio {t.value} {query.city}" for t in query.studio_types
        ] or [f"estúdio criativo {query.city}"]

        seen_ids: set[str] = set()
        async with httpx.AsyncClient(timeout=30) as client:
            for keyword in keywords:
                logger.info(f"[Google Places] Buscando: '{keyword}' em {query.city}/{query.state}")
                async for lead in self._search_term(client, keyword, query, seen_ids):
                    yield lead

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _search_term(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        query: SearchQuery,
        seen_ids: set[str],
    ) -> AsyncIterator[Lead]:
        headers = {
            "X-Goog-Api-Key": self._settings.google_places_api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.internationalPhoneNumber,places.websiteUri,"
                "places.rating,places.userRatingCount,places.types,"
                "places.googleMapsUri,places.location,places.editorialSummary"
            ),
            "Content-Type": "application/json",
        }
        payload = {
            "textQuery": f"{keyword} {query.city} {query.state} Brasil",
            "languageCode": "pt-BR",
            "regionCode": "BR",
            "pageSize": min(query.max_results, 20),
            "locationBias": {
                "circle": {
                    "center": await self._geocode_city(query.city, query.state),
                    "radius": query.radius_km * 1000,
                }
            },
        }

        next_page_token: str | None = None
        fetched = 0

        while fetched < query.max_results:
            if next_page_token:
                payload["pageToken"] = next_page_token

            resp = await client.post(_PLACES_TEXT_SEARCH, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            for place in data.get("places", []):
                place_id = place.get("id", "")
                if place_id in seen_ids:
                    continue
                seen_ids.add(place_id)
                lead = self._parse_place(place)
                if lead:
                    fetched += 1
                    yield lead

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

            await asyncio.sleep(1.2)  # Respeita rate limit

    def _parse_place(self, place: dict) -> Lead | None:
        try:
            name = place.get("displayName", {}).get("text", "").strip()
            if not name:
                return None

            location = place.get("location", {})
            summary = place.get("editorialSummary", {}).get("text")

            return Lead(
                source="google_places",
                external_id=place.get("id"),
                name=name,
                studio_type=_guess_studio_type(name, place.get("types", [])),
                description=summary,
                phone=place.get("internationalPhoneNumber"),
                website=place.get("websiteUri"),
                address=place.get("formattedAddress"),
                latitude=location.get("latitude"),
                longitude=location.get("longitude"),
                rating=place.get("rating"),
                review_count=place.get("userRatingCount"),
                google_maps_url=place.get("googleMapsUri"),
                social=SocialMedia(),
            )
        except Exception as exc:
            logger.warning(f"Erro ao parsear lugar: {exc}")
            return None

    async def _geocode_city(self, city: str, state: str) -> dict:
        """Retorna lat/lng aproximado da cidade via busca rápida."""
        # Coordenadas hardcoded para as principais cidades brasileiras
        _coords: dict[str, dict] = {
            "Tubarão": {"latitude": -23.5505, "longitude": -46.6333},
            "rio de janeiro": {"latitude": -22.9068, "longitude": -43.1729},
            "belo horizonte": {"latitude": -19.9167, "longitude": -43.9345},
            "curitiba": {"latitude": -25.4284, "longitude": -49.2733},
            "porto alegre": {"latitude": -30.0346, "longitude": -51.2177},
            "recife": {"latitude": -8.0476, "longitude": -34.877},
            "salvador": {"latitude": -12.9714, "longitude": -38.5014},
            "fortaleza": {"latitude": -3.7172, "longitude": -38.5433},
            "manaus": {"latitude": -3.119, "longitude": -60.0217},
            "brasília": {"latitude": -15.7801, "longitude": -47.9292},
        }
        return _coords.get(city.lower(), {"latitude": -23.5505, "longitude": -46.6333})
