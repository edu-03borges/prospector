"""Modelos de domínio (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class LeadStatus(str, Enum):
    NOVO = "novo"
    CONTATADO = "contatado"
    QUALIFICADO = "qualificado"
    CONVERTIDO = "convertido"
    PERDIDO = "perdido"
    BLACKLIST = "blacklist"


class StudioType(str, Enum):
    GRAVACAO = "gravacao"
    MUSICA = "musica"
    FOTO = "foto"
    VIDEO = "video"
    PODCAST = "podcast"
    CRIATIVO = "criativo"
    DESCONHECIDO = "desconhecido"


class SocialMedia(BaseModel):
    """Perfis de redes sociais encontrados."""

    instagram: Optional[str] = None
    facebook: Optional[str] = None
    youtube: Optional[str] = None
    tiktok: Optional[str] = None
    linkedin: Optional[str] = None


class Lead(BaseModel):
    """Modelo completo de um lead de estúdio."""

    # Identificação
    id: Optional[int] = None
    source: str = Field(..., description="Fonte: maps_scraper | manual | import")
    external_id: Optional[str] = None   # ID externo da fonte, se disponível

    # Core
    name: str
    studio_type: StudioType = StudioType.DESCONHECIDO
    description: Optional[str] = None

    # Contato
    phone: Optional[str] = None
    email: Optional[str] = None
    email_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    email_is_estimated: bool = False
    website: Optional[str] = None

    # Redes sociais
    social: SocialMedia = Field(default_factory=SocialMedia)

    # Localização
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Métricas Google
    rating: Optional[float] = Field(default=None, ge=0.0, le=5.0)
    review_count: Optional[int] = Field(default=None, ge=0)
    google_maps_url: Optional[str] = None

    # Pipeline de vendas
    status: LeadStatus = LeadStatus.NOVO
    score: int = Field(default=0, ge=0, le=100)
    notes: Optional[str] = None
    last_contacted_at: Optional[datetime] = None
    followup_count: int = 0
    next_followup_at: Optional[datetime] = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        # Remove tudo que não for dígito ou +
        cleaned = "".join(c for c in str(v) if c.isdigit() or c == "+")
        return cleaned if cleaned else None

    @property
    def whatsapp_link(self) -> Optional[str]:
        """Retorna link de abertura do WhatsApp para o telefone."""
        if not self.phone:
            return None
        phone = self.phone.lstrip("+")
        # Adiciona DDI Brasil se necessário
        if not phone.startswith("55") and len(phone) <= 11:
            phone = "55" + phone
        return f"https://wa.me/{phone}"

    @property
    def display_name(self) -> str:
        return self.name.title() if self.name else "Studio Desconhecido"


class SearchQuery(BaseModel):
    """Parâmetros de uma busca de leads."""

    keywords: list[str] = Field(default_factory=list)
    city: str = "Tubarão"
    state: str = "SC"
    radius_km: float = 30.0
    studio_types: list[StudioType] = Field(default_factory=list)
    max_results: int = Field(default=60, ge=1, le=500)
