"""Configurações centrais carregadas de .env e config.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Caminhos base ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[3]   # prospector/
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"
# Garante que pastas existam
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "exports").mkdir(exist_ok=True)
(DATA_DIR / "backups").mkdir(exist_ok=True)

load_dotenv(ROOT / ".env", override=False)


class Settings(BaseSettings):
    """Configurações via variáveis de ambiente (.env)."""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Hunter.io (enriquecimento de e-mail — gratuito até 25/mês)
    hunter_api_key: str = Field(default="", alias="HUNTER_API_KEY")

    # Groq AI (geração de mensagens — gratuito)
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")

    # Banco de dados
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{DATA_DIR}/prospector.db",
        alias="DATABASE_URL",
    )

    # Geral
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"log_level deve ser um de {valid}")
        return v.upper()

    @property
    def has_hunter(self) -> bool:
        return bool(self.hunter_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_yaml_config() -> dict[str, Any]:
    """Carrega config/config.yaml e retorna como dicionário."""
    path = CONFIG_DIR / "config.yaml"
    if not path.exists():
        logger.warning("config.yaml não encontrado, usando padrões embutidos.")
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def cfg(key_path: str, default: Any = None) -> Any:
    """
    Acessa config.yaml via notação de ponto.
    Ex: cfg('search.default_city', 'Tubarão')
    """
    parts = key_path.split(".")
    node: Any = get_yaml_config()
    for part in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(part, default)
        if node is None:
            return default
    return node
