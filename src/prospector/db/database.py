"""Camada de acesso ao banco de dados (SQLAlchemy async)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Sequence

from loguru import logger
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    case,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from prospector.config.settings import cfg, get_settings
from prospector.models.lead import Lead, LeadStatus, SocialMedia, StudioType


# ── ORM ──────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class LeadORM(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("name", "city", name="uq_lead_name_city"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)
    external_id = Column(String(200), nullable=True, unique=True)

    name = Column(String(300), nullable=False)
    studio_type = Column(String(50), default="desconhecido")
    description = Column(Text, nullable=True)

    phone = Column(String(30), nullable=True)
    email = Column(String(200), nullable=True)
    email_confidence = Column(Float, default=0.0)
    email_is_estimated = Column(Boolean, default=False)
    website = Column(String(500), nullable=True)
    social = Column(JSON, default=dict)

    address = Column(String(500), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(10), nullable=True)
    zip_code = Column(String(20), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    rating = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=True)
    google_maps_url = Column(String(500), nullable=True)

    status = Column(String(30), default="novo")
    score = Column(Integer, default=0)
    notes = Column(Text, nullable=True)
    last_contacted_at = Column(DateTime, nullable=True)
    followup_count = Column(Integer, default=0)
    next_followup_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Engine & Session ──────────────────────────────────────────────────────────

_engine = None
_session_factory = None


async def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        database_url = settings.database_url
        url = make_url(database_url)
        engine_kwargs: dict[str, Any] = {
            "echo": False,
            "pool_pre_ping": True,
            "connect_args": {"statement_cache_size": 0},
        }
        if url.host and url.host.endswith(".pooler.supabase.com"):
            engine_kwargs["poolclass"] = NullPool
        _engine = create_async_engine(
            database_url,
            **engine_kwargs,
        )
        try:
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        except Exception as exc:
            _engine = None
            host = url.host or "host-desconhecido"
            msg = str(exc)
            if "DuplicatePreparedStatementError" in msg or "prepared statement" in msg.lower():
                raise RuntimeError(
                    "A conexao chegou no Supabase, mas o pooler rejeitou prepared statements. "
                    "O cliente precisa desabilitar cache de statements para PgBouncer."
                ) from exc
            raise RuntimeError(
                "Nao foi possivel conectar ao banco. "
                f"Host atual: {host}. "
                "Se for Supabase, copie a connection string inteira no botao Connect "
                "e cole em DATABASE_URL; nao monte o host manualmente."
            ) from exc
        logger.info(f"Banco conectado: {database_url}")
    return _engine


async def get_session() -> AsyncSession:
    global _session_factory
    if _session_factory is None:
        engine = await get_engine()
        _session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _session_factory()


# ── Conversores ───────────────────────────────────────────────────────────────

def _orm_to_lead(row: LeadORM) -> Lead:
    social_data = row.social or {}
    if isinstance(social_data, str):
        social_data = json.loads(social_data)
    return Lead(
        id=row.id,
        source=row.source,
        external_id=row.external_id,
        name=row.name,
        studio_type=StudioType(row.studio_type),
        description=row.description,
        phone=row.phone,
        email=row.email,
        email_confidence=row.email_confidence or 0.0,
        email_is_estimated=row.email_is_estimated or False,
        website=row.website,
        social=SocialMedia(**social_data),
        address=row.address,
        city=row.city,
        state=row.state,
        zip_code=row.zip_code,
        latitude=row.latitude,
        longitude=row.longitude,
        rating=row.rating,
        review_count=row.review_count,
        google_maps_url=row.google_maps_url,
        status=LeadStatus(row.status),
        score=row.score or 0,
        notes=row.notes,
        last_contacted_at=row.last_contacted_at,
        followup_count=row.followup_count or 0,
        next_followup_at=row.next_followup_at,
        created_at=row.created_at or datetime.utcnow(),
        updated_at=row.updated_at or datetime.utcnow(),
    )


def _lead_to_orm_dict(lead: Lead) -> dict:
    return {
        "source": lead.source,
        "external_id": lead.external_id,
        "name": lead.name,
        "studio_type": lead.studio_type.value,
        "description": lead.description,
        "phone": lead.phone,
        "email": lead.email,
        "email_confidence": lead.email_confidence,
        "email_is_estimated": lead.email_is_estimated,
        "website": lead.website,
        "social": lead.social.model_dump(),
        "address": lead.address,
        "city": lead.city,
        "state": lead.state,
        "zip_code": lead.zip_code,
        "latitude": lead.latitude,
        "longitude": lead.longitude,
        "rating": lead.rating,
        "review_count": lead.review_count,
        "google_maps_url": lead.google_maps_url,
        "status": lead.status.value,
        "score": lead.score,
        "notes": lead.notes,
        "last_contacted_at": lead.last_contacted_at,
        "followup_count": lead.followup_count,
        "next_followup_at": lead.next_followup_at,
        "updated_at": datetime.utcnow(),
    }


# ── Repositório ───────────────────────────────────────────────────────────────

class LeadRepository:
    """CRUD básico para leads."""

    async def upsert(self, lead: Lead, *, protect_manual_fields: bool = True) -> tuple[Lead, bool]:
        """Insere ou atualiza o lead. Retorna (lead_salvo, foi_criado)."""
        async with await get_session() as session:
            # Detecta duplicata por external_id ou (name + city)
            existing = None
            if lead.external_id:
                stmt = select(LeadORM).where(LeadORM.external_id == lead.external_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

            if existing is None:
                stmt = select(LeadORM).where(
                    and_(
                        func.lower(LeadORM.name) == lead.name.lower(),
                        func.lower(LeadORM.city) == (lead.city or "").lower(),
                    )
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

            if existing:
                # Atualiza com dados frescos, mas protege o status comercial do Lead
                data = _lead_to_orm_dict(lead)
                protected_fields = {"status", "notes"} if protect_manual_fields else set()
                for col, val in data.items():
                    if col in protected_fields:  # Não sobrescreve o trabalho manual
                        continue
                    if val is not None and val != getattr(existing, col):
                        setattr(existing, col, val)
                existing.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(existing)
                return _orm_to_lead(existing), False

            row = LeadORM(**_lead_to_orm_dict(lead), created_at=datetime.utcnow())
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _orm_to_lead(row), True

    async def get_all(
        self,
        status: Optional[LeadStatus] = None,
        city: Optional[str] = None,
        min_score: int = 0,
        limit: int = 1000,
        query_text: Optional[str] = None,
        has_email: Optional[bool] = None,
        has_phone: Optional[bool] = None,
    ) -> Sequence[Lead]:
        async with await get_session() as session:
            stmt = select(LeadORM).order_by(LeadORM.score.desc())
            if status:
                stmt = stmt.where(LeadORM.status == status.value)
            if city:
                stmt = stmt.where(func.lower(LeadORM.city) == city.lower())
            if min_score:
                stmt = stmt.where(LeadORM.score >= min_score)
            if query_text:
                like_term = f"%{query_text.strip().lower()}%"
                stmt = stmt.where(
                    or_(
                        func.lower(LeadORM.name).like(like_term),
                        func.lower(func.coalesce(LeadORM.city, "")).like(like_term),
                        func.lower(func.coalesce(LeadORM.state, "")).like(like_term),
                        func.lower(func.coalesce(LeadORM.email, "")).like(like_term),
                        func.lower(func.coalesce(LeadORM.website, "")).like(like_term),
                    )
                )
            if has_email is True:
                stmt = stmt.where(LeadORM.email.is_not(None), LeadORM.email != "")
            elif has_email is False:
                stmt = stmt.where(or_(LeadORM.email.is_(None), LeadORM.email == ""))
            if has_phone is True:
                stmt = stmt.where(LeadORM.phone.is_not(None), LeadORM.phone != "")
            elif has_phone is False:
                stmt = stmt.where(or_(LeadORM.phone.is_(None), LeadORM.phone == ""))
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [_orm_to_lead(r) for r in result.scalars().all()]

    async def get_by_id(self, lead_id: int) -> Optional[Lead]:
        async with await get_session() as session:
            stmt = select(LeadORM).where(LeadORM.id == lead_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _orm_to_lead(row) if row else None

    async def update_status(self, lead_id: int, status: LeadStatus, notes: Optional[str] = None) -> None:
        async with await get_session() as session:
            values: dict = {"status": status.value, "updated_at": datetime.utcnow()}
            if notes is not None:
                values["notes"] = notes
            await session.execute(update(LeadORM).where(LeadORM.id == lead_id).values(**values))
            await session.commit()

    async def count_by_status(self) -> dict[str, int]:
        async with await get_session() as session:
            stmt = select(LeadORM.status, func.count(LeadORM.id)).group_by(LeadORM.status)
            result = await session.execute(stmt)
            return {row[0]: row[1] for row in result.all()}

    async def get_pending_followups(self) -> Sequence[Lead]:
        async with await get_session() as session:
            now = datetime.utcnow()
            max_followups = int(cfg("outreach.max_followups", 2))
            stmt = (
                select(LeadORM)
                .where(
                    and_(
                        LeadORM.next_followup_at <= now,
                        LeadORM.status == LeadStatus.CONTATADO.value,
                        LeadORM.followup_count < max_followups,
                    )
                )
                .order_by(LeadORM.next_followup_at)
            )
            result = await session.execute(stmt)
            return [_orm_to_lead(r) for r in result.scalars().all()]

    async def add_to_blacklist(self, name: str, city: str) -> None:
        async with await get_session() as session:
            stmt = (
                update(LeadORM)
                .where(
                    and_(
                        func.lower(LeadORM.name) == name.lower(),
                        func.lower(LeadORM.city) == city.lower(),
                    )
                )
                .values(status=LeadStatus.BLACKLIST.value, updated_at=datetime.utcnow())
            )
            await session.execute(stmt)
            await session.commit()

    async def update_lead(
        self,
        lead_id: int,
        *,
        status: Optional[LeadStatus] = None,
        notes: Optional[str] = None,
        next_followup_at: Optional[datetime] = None,
        clear_next_followup: bool = False,
    ) -> None:
        async with await get_session() as session:
            values: dict[str, Any] = {"updated_at": datetime.utcnow()}
            if status is not None:
                values["status"] = status.value
            if notes is not None:
                values["notes"] = notes
            if next_followup_at is not None:
                values["next_followup_at"] = next_followup_at
            elif clear_next_followup:
                values["next_followup_at"] = None
            await session.execute(update(LeadORM).where(LeadORM.id == lead_id).values(**values))
            await session.commit()

    async def delete(self, lead_id: int) -> bool:
        async with await get_session() as session:
            row = await session.get(LeadORM, lead_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def mark_contacted(
        self,
        lead_id: int,
        *,
        notes: Optional[str] = None,
        next_followup_at: Optional[datetime] = None,
    ) -> None:
        async with await get_session() as session:
            values: dict[str, Any] = {
                "status": LeadStatus.CONTATADO.value,
                "last_contacted_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            if notes is not None:
                values["notes"] = notes
            if next_followup_at is not None:
                values["next_followup_at"] = next_followup_at
            await session.execute(update(LeadORM).where(LeadORM.id == lead_id).values(**values))
            await session.commit()

    async def register_followup_sent(
        self,
        lead_id: int,
        *,
        next_followup_at: Optional[datetime] = None,
        notes: Optional[str] = None,
    ) -> None:
        async with await get_session() as session:
            values: dict[str, Any] = {
                "status": LeadStatus.CONTATADO.value,
                "last_contacted_at": datetime.utcnow(),
                "followup_count": LeadORM.followup_count + 1,
                "updated_at": datetime.utcnow(),
            }
            if notes is not None:
                values["notes"] = notes
            values["next_followup_at"] = next_followup_at
            await session.execute(update(LeadORM).where(LeadORM.id == lead_id).values(**values))
            await session.commit()

    async def get_pipeline_snapshot(self) -> dict[str, Any]:
        async with await get_session() as session:
            now = datetime.utcnow()
            stmt = select(
                func.count(LeadORM.id),
                func.coalesce(func.avg(LeadORM.score), 0),
                func.sum(
                    case(
                        (
                            and_(LeadORM.email.is_not(None), LeadORM.email != ""),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            and_(LeadORM.phone.is_not(None), LeadORM.phone != ""),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            and_(
                                LeadORM.status == LeadStatus.NOVO.value,
                                LeadORM.score >= 75,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            and_(
                                LeadORM.status == LeadStatus.CONTATADO.value,
                                LeadORM.next_followup_at.is_not(None),
                                LeadORM.next_followup_at <= now,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            result = await session.execute(stmt)
            total, avg_score, with_email, with_phone, hot_new, followups_due = result.one()
            return {
                "total": total or 0,
                "avg_score": round(float(avg_score or 0), 1),
                "with_email": with_email or 0,
                "with_phone": with_phone or 0,
                "hot_new": hot_new or 0,
                "followups_due": followups_due or 0,
            }
