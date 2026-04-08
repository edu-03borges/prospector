"""Motor de outreach via WhatsApp Web — 100% gratuito via Playwright."""

from __future__ import annotations

import asyncio
import hashlib
import time
import urllib.parse
import unicodedata
from contextlib import suppress
from datetime import datetime, timedelta

from loguru import logger

from prospector.config.settings import cfg, get_settings
from prospector.db.database import LeadRepository
from prospector.models.lead import Lead, LeadStatus

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


def _template_variant_index(lead: Lead, is_followup: bool, total: int) -> int:
    seed = f"{lead.id or lead.name}|{lead.city or ''}|{'fup' if is_followup else 'first'}"
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % total


def _sender_name() -> str:
    sender_name = str(cfg("outreach.whatsapp_sender_name", "Fulanik")).strip()
    return sender_name or "Fulanik"


def _sender_company() -> str:
    sender_company = str(cfg("outreach.whatsapp_sender_company", "Elarin")).strip()
    return sender_company or "Elarin"


def _default_message(lead: Lead, is_followup: bool) -> str:
    studio_name = lead.name
    sender_name = _sender_name()
    sender_company = _sender_company()

    first_contact_message = (
        f"Oii, sou o {sender_name}, da {sender_company} 🧩\n"
        f"uma startup de alta tecnologia e estamos escolhendo alguns studios pra serem pilotos com a gente\n"
        f"temos parceria com o Instituto Pullsa (Complexo Médico Pró Vida, Laboratório Santa Catarina, Via Laser e GAM) e Sigma Park\n"
        f"vi o espaço de vcs e fez mt sentido pra gente\n"
        f"topa trocar uma ideia rápida?\n"
        f"(Sou eu msm q to mandando a msg, n é IA rsrs)"
    )

    followup_variants = [
        (
            f"Oi, tudo bem?\n"
            f"Passando de novo porque acho que isso pode fazer sentido pro {studio_name}.\n"
            f"Quer que eu te mande um vídeo curto?"
        ),
        (
            f"Oi, retomando rapidinho.\n"
            f"Fiquei achando que isso pode encaixar no {studio_name}.\n"
            f"Faz sentido eu te mostrar?"
        ),
        (
            f"Oi, tudo certo?\n"
            f"Só voltando porque talvez isso seja útil pro {studio_name}.\n"
            f"Se quiser, te explico bem rápido por aqui."
        ),
    ]

    if not is_followup:
        return first_contact_message

    return followup_variants[_template_variant_index(lead, is_followup, len(followup_variants))]


# ── Gerador de mensagem (Groq AI ou template) ─────────────────────────────────

async def _generate_whatsapp_message(lead: Lead, is_followup: bool = False) -> str:
    """Gera mensagem curta e natural para WhatsApp via Groq ou template embutido."""
    settings = get_settings()
    sender_name = _sender_name()
    sender_company = _sender_company()

    if not is_followup:
        return _default_message(lead, is_followup=False)

    if settings.has_groq:
        try:
            import httpx
            prompt = (
                f"Escreva uma mensagem de WhatsApp em português brasileiro para "
                f"{'follow-up' if is_followup else 'primeiro contato'} com '{lead.name}', "
                f"um estúdio de {lead.studio_type.value} em {lead.city}. "
                f"O remetente é {sender_name}, da {sender_company}.\n\n"
                f"Objetivo: abrir uma conversa e propor uma demonstração curta.\n\n"
                f"Regras obrigatórias:\n"
                f"- soar como mensagem escrita manualmente por um fundador no WhatsApp\n"
                f"- 2 ou 3 linhas curtas\n"
                f"- no máximo 220 caracteres\n"
                f"- linguagem simples, direta e humana\n"
                f"- não usar jargões de IA ou vendas\n"
                f"- evitar frases como 'agregar valor', 'diferencial', 'solução inovadora', "
                f"'visão computacional', 'sem wearables'\n"
                f"- sem emoji\n"
                f"- terminar com uma pergunta curta\n"
                f"- não usar aspas, assunto, lista, nem cara de texto gerado por IA\n\n"
                f"Contexto real do produto: a {sender_company} acompanha postura e movimento usando a câmera."
            )
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    _GROQ_CHAT_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    json={
                        "model": "llama3-8b-8192",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.55,
                    },
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"].strip()
                logger.debug(f"[Groq/WA] Mensagem gerada para {lead.name}")
                return text
        except Exception as exc:
            logger.warning(f"[Groq/WA] Falha, usando template padrão: {exc}")

    return _default_message(lead, is_followup)


# ── WhatsApp Web via Playwright ───────────────────────────────────────────────

class WhatsAppWebSender:
    """
    Envia mensagens via WhatsApp Web abrindo o Chromium via Playwright.

    Na primeira execução, abre o navegador para escanear o QR code.
    Nas próximas, o login fica salvo no perfil persistente (data/whatsapp_session).

    ⚠ Use com responsabilidade — respeite os delays para não arriscar ban.
    """

    max_per_session: int = cfg("outreach.whatsapp_max_per_session", 20)
    delay_seconds: float = cfg("outreach.whatsapp_delay_seconds", 30)
    max_per_hour: int = cfg("outreach.whatsapp_max_per_hour", 30)
    ready_timeout_ms: int = int(cfg("outreach.whatsapp_ready_timeout_ms", 90_000))
    send_timeout_ms: int = int(cfg("outreach.whatsapp_send_timeout_ms", 10_000))

    _INVALID_NUMBER_FRAGMENTS = (
        "phone number shared via url is invalid",
        "shared via url is invalid",
        "numero de telefone compartilhado por url e invalido",
        "numero de telefone compartilhado pela url e invalido",
        "numero de telefone nao esta no whatsapp",
        "numero nao esta no whatsapp",
        "numero de telefone nao existe no whatsapp",
        "phone number is not on whatsapp",
        "phone number isnt on whatsapp",
        "phone number isn't on whatsapp",
    )
    _TRANSIENT_ERROR_FRAGMENTS = (
        "tente novamente",
        "try again",
        "algo deu errado",
        "something went wrong",
        "nao foi possivel abrir a conversa",
        "couldnt open the chat",
        "couldn't open the chat",
    )

    def __init__(self) -> None:
        self._sent_count = 0
        self._sent_times: list[float] = []

    def _rate_ok(self) -> bool:
        now = time.time()
        self._sent_times = [t for t in self._sent_times if now - t < 3600]
        return (
            len(self._sent_times) < self.max_per_hour
            and self._sent_count < self.max_per_session
        )

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        phone = "".join(c for c in phone if c.isdigit() or c == "+")
        phone = phone.lstrip("+")
        if not phone.startswith("55") and len(phone) <= 11:
            phone = "55" + phone
        return phone

    @staticmethod
    def _has_supported_phone_format(phone: str) -> bool:
        return phone.isdigit() and 12 <= len(phone) <= 13

    @staticmethod
    def _simplify_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text or "")
        return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()

    @classmethod
    def _looks_invalid_number_message(cls, text: str) -> bool:
        return any(fragment in text for fragment in cls._INVALID_NUMBER_FRAGMENTS)

    @classmethod
    def _looks_transient_error_message(cls, text: str) -> bool:
        return any(fragment in text for fragment in cls._TRANSIENT_ERROR_FRAGMENTS)

    async def _safe_body_text(self, page) -> str:
        try:
            return await page.locator("body").inner_text(timeout=1_000)
        except Exception:
            return ""

    async def _is_chat_ready(self, page) -> bool:
        selectors = (
            "div[contenteditable='true'][data-tab='10']",
            "footer div[contenteditable='true']",
            "button[data-testid='compose-btn-send']",
        )
        for selector in selectors:
            locator = page.locator(selector).first
            with suppress(Exception):
                if await locator.is_visible():
                    return True
        return False

    async def _wait_for_chat_state(self, page, dialog_state: dict[str, str | None]) -> tuple[bool, str | None]:
        deadline = time.monotonic() + (self.ready_timeout_ms / 1000)
        while time.monotonic() < deadline:
            dialog_message = dialog_state.get("message")
            if dialog_message:
                dialog_text = self._simplify_text(dialog_message)
                if self._looks_invalid_number_message(dialog_text):
                    return False, "numero invalido ou sem WhatsApp"
                return False, f"dialogo inesperado: {dialog_message}"

            if await self._is_chat_ready(page):
                return True, None

            body_text = self._simplify_text(await self._safe_body_text(page))
            if self._looks_invalid_number_message(body_text):
                return False, "numero invalido ou sem WhatsApp"
            if self._looks_transient_error_message(body_text):
                return False, "falha ao abrir a conversa no WhatsApp Web"
            await asyncio.sleep(1)

        return False, "tempo limite ao abrir a conversa"

    def build_wa_url(self, phone: str, message: str) -> str:
        """Retorna URL wa.me com mensagem pré-preenchida."""
        phone = self._normalize_phone(phone)
        return f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"

    def build_web_url(self, phone: str, message: str) -> str:
        """URL do WhatsApp Web com mensagem pré-preenchida (para uso no browser)."""
        phone = self._normalize_phone(phone)
        return f"https://web.whatsapp.com/send?phone={phone}&text={urllib.parse.quote(message)}"

    async def send(self, lead: Lead, message: str) -> bool:
        """
        Abre WhatsApp Web via Playwright e envia a mensagem automaticamente.
        O perfil do browser é salvo localmente para não pedir QR code toda vez.
        """
        if not self._rate_ok():
            logger.warning("[WA Web] Rate limit atingido. Aguarde antes de continuar.")
            return False

        if not lead.phone:
            logger.warning(f"[WA Web] Lead '{lead.name}' sem telefone. Pulando.")
            return False

        phone = self._normalize_phone(lead.phone)
        if not self._has_supported_phone_format(phone):
            logger.warning(
                f"[WA Web] Telefone invalido para '{lead.name}' ({lead.phone}). "
                f"Pulando sem abrir o navegador."
            )
            return False

        ctx = None
        try:
            from playwright.async_api import async_playwright
            from prospector.config.settings import DATA_DIR

            session_dir = str(DATA_DIR / "whatsapp_session")
            wa_url = self.build_web_url(phone, message)

            async with async_playwright() as pw:
                # Perfil persistente = login salvo entre execuções
                ctx = await pw.chromium.launch_persistent_context(
                    user_data_dir=session_dir,
                    headless=False,          # Visível: necessário para QR code na 1ª vez
                    args=["--no-sandbox"],
                    viewport={"width": 1200, "height": 800},
                )

                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                dialog_state: dict[str, str | None] = {"message": None}

                async def _handle_dialog(dialog) -> None:
                    dialog_state["message"] = dialog.message or ""
                    with suppress(Exception):
                        await dialog.dismiss()

                page.on("dialog", lambda dialog: asyncio.create_task(_handle_dialog(dialog)))
                await page.goto(wa_url, wait_until="domcontentloaded", timeout=60_000)

                logger.info(
                    f"[WA Web] Conectando para {lead.name}... "
                    f"(1ª vez? Escaneie o QR code no browser)"
                )

                ready, reason = await self._wait_for_chat_state(page, dialog_state)
                if not ready:
                    logger.warning(
                        f"[WA Web] Pulando {lead.name} ({phone}): {reason}. "
                        f"Navegador fechado automaticamente."
                    )
                    return False

                await asyncio.sleep(2)

                # Tenta clicar no botão de enviar
                send_btn = page.locator("button[data-testid='compose-btn-send']").first
                if await send_btn.is_visible():
                    await send_btn.click(timeout=self.send_timeout_ms)
                else:
                    await page.keyboard.press("Enter")

                await asyncio.sleep(2)
                self._sent_count += 1
                self._sent_times.append(time.time())
                logger.success(f"[WA Web] ✓ Mensagem enviada para {lead.name} ({phone})")
                return True

        except Exception as exc:
            logger.error(f"[WA Web] Falha ao enviar para {lead.name}: {exc}")
            return False
        finally:
            if ctx is not None:
                with suppress(Exception):
                    await ctx.close()

    def generate_wa_links_only(self, leads: list[Lead], message: str) -> list[dict]:
        """Gera lista de links wa.me para envio manual pelo celular."""
        result = []
        for lead in leads:
            if lead.phone:
                result.append({
                    "nome": lead.name,
                    "cidade": lead.city or "—",
                    "score": lead.score,
                    "telefone": lead.phone,
                    "link_whatsapp": self.build_wa_url(lead.phone, message),
                })
        return result


# ── Engine principal ──────────────────────────────────────────────────────────

class WhatsAppEngine:
    """Orquestra campanhas de WhatsApp via WhatsApp Web (100% gratuito)."""

    def __init__(self) -> None:
        self._sender = WhatsAppWebSender()
        self._repo = LeadRepository()

    async def send_to_lead(
        self, lead: Lead, is_followup: bool = False, custom_message: str | None = None
    ) -> bool:
        """Gera e envia mensagem para um lead."""
        if lead.status == LeadStatus.BLACKLIST:
            logger.warning(f"[WA] Lead '{lead.name}' está na blacklist. Pulando envio.")
            return False
        message = custom_message or await _generate_whatsapp_message(lead, is_followup)
        ok = await self._sender.send(lead, message)
        if ok and lead.id:
            followup_delay_days = int(cfg("outreach.followup_after_days", 5))
            next_followup = datetime.utcnow() + timedelta(days=followup_delay_days)
            if is_followup:
                max_followups = int(cfg("outreach.max_followups", 2))
                next_date = next_followup if lead.followup_count + 1 < max_followups else None
                await self._repo.register_followup_sent(
                    lead.id,
                    next_followup_at=next_date,
                )
            else:
                await self._repo.mark_contacted(
                    lead.id,
                    next_followup_at=next_followup,
                )
        return ok

    async def run_campaign(
        self,
        min_score: int = 50,
        limit: int = 10,
        is_followup: bool = False,
        custom_message: str | None = None,
    ) -> tuple[int, int]:
        """Dispara campanha em lote. Retorna (enviados, falhas)."""
        repo = LeadRepository()
        if is_followup:
            leads = await repo.get_pending_followups()
            leads = [l for l in leads if l.phone][:limit]
        else:
            leads = await repo.get_all(
                status=LeadStatus.NOVO, min_score=min_score, limit=limit
            )
            leads = [l for l in leads if l.phone]

        if not leads:
            logger.warning("[WA] Nenhum lead com telefone encontrado.")
            return 0, 0

        sent, failed = 0, 0
        delay = cfg("outreach.whatsapp_delay_seconds", 30)

        for i, lead in enumerate(leads):
            ok = await self.send_to_lead(lead, is_followup, custom_message)
            if ok:
                sent += 1
            else:
                failed += 1
            if i < len(leads) - 1:
                logger.debug(f"[WA] Aguardando {delay}s...")
                await asyncio.sleep(delay)

        return sent, failed

    def generate_wa_links(
        self, leads: list[Lead], message_template: str | None = None
    ) -> list[dict]:
        """Gera links wa.me para disparo manual pelo celular."""
        eligible = [lead for lead in leads if lead.status != LeadStatus.BLACKLIST]
        if not eligible:
            return []
        return self._sender.generate_wa_links_only(
            eligible,
            message_template or _default_message(eligible[0], is_followup=False),
        )
