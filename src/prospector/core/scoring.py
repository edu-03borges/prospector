"""Motor de scoring inteligente para leads de estúdios."""

from __future__ import annotations

from prospector.config.settings import cfg
from prospector.models.lead import Lead


_WEIGHTS: dict[str, int] = {
    "has_email": 30,
    "has_phone": 20,
    "has_website": 15,
    "has_instagram": 10,
    "high_rating": 10,
    "many_reviews": 10,
    "near_target_city": 5,
}


def score_lead(lead: Lead, target_city: str | None = None) -> int:
    """
    Calcula o score de 0-100 do lead com base em critérios comerciais.
    Quanto maior o score, mais promissor é o lead para o iMotio.
    """
    weights = cfg("scoring.weights", _WEIGHTS)
    total = 0

    # Contato disponível
    if lead.email and not lead.email_is_estimated:
        total += weights.get("has_email", 30)
    elif lead.email and lead.email_is_estimated:
        total += weights.get("has_email", 30) // 2  # metade se estimado

    if lead.phone:
        total += weights.get("has_phone", 20)

    if lead.website:
        total += weights.get("has_website", 15)

    if lead.social.instagram:
        total += weights.get("has_instagram", 10)

    # Qualidade Google
    if lead.rating is not None and lead.rating >= 4.5:
        total += weights.get("high_rating", 10)

    if lead.review_count is not None and lead.review_count >= 50:
        total += weights.get("many_reviews", 10)

    # Proximidade geográfica
    if target_city and lead.city:
        if target_city.lower() in lead.city.lower() or lead.city.lower() in target_city.lower():
            total += weights.get("near_target_city", 5)

    return min(total, 100)


def classify_lead_priority(score: int) -> str:
    """Retorna rótulo de prioridade baseado no score."""
    if score >= 75:
        return "🔥 Alta"
    if score >= 45:
        return "⚡ Média"
    return "🧊 Baixa"
