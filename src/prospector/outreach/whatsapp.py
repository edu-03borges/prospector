"""Motor de outreach via WhatsApp Web — 100% gratuito via Playwright."""

from __future__ import annotations

import asyncio
import time
import urllib.parse

from loguru import logger

from prospector.config.settings import cfg, get_settings
from prospector.db.database import LeadRepository
from prospector.models.lead import Lead, LeadStatus

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


# ── Gerador de mensagem (Groq AI ou template) ─────────────────────────────────

async def _generate_whatsapp_message(lead: Lead, is_followup: bool = False) -> str:
    """Gera mensagem curta e natural para WhatsApp via Groq ou template embutido."""
    settings = get_settings()

    if settings.has_groq:
        try:
            import httpx
            prompt = (
                f"Escreva uma mensagem de WhatsApp curta (máx. 5 linhas) em português brasileiro "
                f"para um {'follow-up' if is_followup else 'primeiro contato'} com o(a) "
                f"'{lead.name}', um estúdio de {lead.studio_type.value} em {lead.city}. "
                f"O remetente é Eduardo, fundador do iMotio (tecnologia de visão computacional "
                f"para correção de postura em exercícios físicos). "
                f"Seja direto, simpático e natural — como um empresário que manda mensagem no zap. "
                f"Não use emojis em excesso. Finalize com uma pergunta para gerar resposta."
            )
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    _GROQ_CHAT_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    json={
                        "model": "llama3-8b-8192",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.85,
                    },
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"].strip()
                logger.debug(f"[Groq/WA] Mensagem gerada para {lead.name}")
                return text
        except Exception as exc:
            logger.warning(f"[Groq/WA] Falha, usando template padrão: {exc}")

    # Template padrão embutido
    if is_followup:
        return (
            f"Oi, tudo bem? 👋\n"
            f"Passei para retomar o contato sobre o iMotio.\n"
            f"Ainda tenho interesse em mostrar como nossa tecnologia pode agregar valor para "
            f"o {lead.name}.\n"
            f"Topa 15 minutos essa semana para eu apresentar? 🙏"
        )
    return (
        f"Olá! Meu nome é Eduardo, sou fundador do iMotio 👋\n"
        f"Desenvolvemos uma tecnologia de visão computacional que usa câmera comum para "
        f"corrigir postura e analisar movimentos em tempo real — sem wearables.\n"
        f"Vi que o {lead.name} é um espaço incrível em {lead.city} e acredito que nossa "
        f"solução pode ser um diferencial real para os clientes de vocês.\n"
        f"Posso mostrar em 15 minutos como funciona? 🚀"
    )


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

    def build_wa_url(self, phone: str, message: str) -> str:
        """Retorna URL wa.me com mensagem pré-preenchida."""
        phone = phone.lstrip("+").replace(" ", "").replace("-", "")
        if not phone.startswith("55") and len(phone) <= 11:
            phone = "55" + phone
        return f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"

    def build_web_url(self, phone: str, message: str) -> str:
        """URL do WhatsApp Web com mensagem pré-preenchida (para uso no browser)."""
        phone = phone.lstrip("+").replace(" ", "").replace("-", "")
        if not phone.startswith("55") and len(phone) <= 11:
            phone = "55" + phone
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

        phone = lead.phone.lstrip("+").replace(" ", "").replace("-", "")
        if not phone.startswith("55") and len(phone) <= 11:
            phone = "55" + phone

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
                await page.goto(wa_url, wait_until="domcontentloaded", timeout=60_000)

                logger.info(
                    f"[WA Web] Conectando para {lead.name}... "
                    f"(1ª vez? Escaneie o QR code no browser)"
                )

                # Aguarda a caixa de texto aparecer (até 60s — carrega QR + conversa)
                send_box = page.locator("div[contenteditable='true'][data-tab='10']")
                try:
                    await send_box.wait_for(state="visible", timeout=60_000)
                except Exception:
                    # Fallback: aguarda qualquer input editável
                    await asyncio.sleep(8)

                await asyncio.sleep(2)

                # Tenta clicar no botão de enviar
                send_btn = page.locator("button[data-testid='compose-btn-send']")
                if await send_btn.count() > 0:
                    await send_btn.click()
                else:
                    await page.keyboard.press("Enter")

                await asyncio.sleep(2)
                self._sent_count += 1
                self._sent_times.append(time.time())
                logger.success(f"[WA Web] ✓ Mensagem enviada para {lead.name} ({phone})")
                await ctx.close()
                return True

        except Exception as exc:
            logger.error(f"[WA Web] Falha ao enviar para {lead.name}: {exc}")
            return False

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
        message = custom_message or await _generate_whatsapp_message(lead, is_followup)
        ok = await self._sender.send(lead, message)
        if ok and lead.id:
            await self._repo.update_status(lead.id, LeadStatus.CONTATADO)
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
        default_msg = (
            "Olá! Vi o estúdio de vocês e gostaria de apresentar o iMotio, "
            "uma tecnologia de IA para análise de movimento. "
            "Posso mostrar em 15 minutos? 🙏"
        )
        return self._sender.generate_wa_links_only(leads, message_template or default_msg)
