"""Motor de outreach: envio de e-mails personalizados via SMTP + Groq AI."""

from __future__ import annotations

import asyncio
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from loguru import logger

from prospector.config.settings import TEMPLATES_DIR, cfg, get_settings
from prospector.db.database import LeadRepository
from prospector.models.lead import Lead, LeadStatus

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class OutreachEngine:
    """
    Motor de envio de e-mails personalizados para leads.

    ⚠ AVISO LGPD:
    - Use apenas para contato inicial com pessoa jurídica (estúdios).
    - Inclua sempre a opção de "não receber mais e-mails" (opt-out).
    - Nunca compartilhe dados com terceiros.
    - Mantenha registro de consentimento.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._repo = LeadRepository()

        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        self._jinja = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
        )
        self._max_per_hour: int = cfg("outreach.max_emails_per_hour", 15)
        self._min_delay: int = cfg("outreach.min_send_delay_seconds", 240)
        self._sent_this_hour: list[float] = []

    def _get_template(self, template_name: str) -> tuple[str, str]:
        """Retorna (html_body, text_body) do template Jinja2."""
        try:
            html_tpl = self._jinja.get_template(f"{template_name}.html")
            return html_tpl, None  # type: ignore
        except Exception:
            logger.warning(f"Template '{template_name}.html' não encontrado, usando padrão embutido.")
            return None, None  # type: ignore

    def _rate_limit_ok(self) -> bool:
        now = time.time()
        # Remove timestamps mais antigos que 1 hora
        self._sent_this_hour = [t for t in self._sent_this_hour if now - t < 3600]
        return len(self._sent_this_hour) < self._max_per_hour

    async def generate_message_with_ai(self, lead: Lead, is_followup: bool = False) -> str:
        """Gera corpo de e-mail personalizado via Groq (llama-3-8b)."""
        if not self._settings.has_groq:
            return self._default_message(lead, is_followup)

        import httpx

        prompt = (
            f"Escreva um e-mail comercial curto (máx. 120 palavras) em português brasileiro "
            f"para um {'follow-up' if is_followup else 'primeiro contato'} com o(a) "
            f"'{lead.name}', um(a) estúdio de {lead.studio_type.value} localizado em {lead.city}. "
            f"O remetente é Eduardo da startup iMotio, que desenvolve tecnologia de "
            f"visão computacional para correção de postura em exercícios físicos. "
            f"O objetivo é marcar uma reunião de 15 minutos. Seja direto, cordial e criativo. "
            f"Não use linguagem robótica. Inclua assunto na primeira linha com prefixo 'Assunto: '."
        )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    _GROQ_CHAT_URL,
                    headers={"Authorization": f"Bearer {self._settings.groq_api_key}"},
                    json={
                        "model": "llama3-8b-8192",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.8,
                    },
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                logger.debug(f"[Groq] Mensagem gerada para {lead.name}")
                return text
        except Exception as exc:
            logger.warning(f"[Groq] Falha, usando template padrão: {exc}")
            return self._default_message(lead, is_followup)

    def _default_message(self, lead: Lead, is_followup: bool) -> str:
        """Template padrão inline quando Groq não está disponível."""
        if is_followup:
            return (
                f"Assunto: [Follow-up] iMotio — ainda tenho interesse em conversar sobre {lead.name}\n\n"
                f"Olá!\n\nPassei para dar um olá e ver se há algum momento esta semana para "
                f"conversarmos rapidamente sobre como o iMotio pode agregar valor para o {lead.name}.\n\n"
                f"São apenas 15 minutos — pode ser por videochamada ou ao vivo.\n\n"
                f"Aguardo seu retorno!\n\nEduardo\niMotio | IA para Movimento Humano"
            )
        return (
            f"Assunto: {lead.name} + iMotio: tecnologia de IA que pode transformar seu espaço\n\n"
            f"Olá, equipe {lead.name}!\n\n"
            f"Meu nome é Eduardo e desenvolvi uma tecnologia de visão computacional chamada "
            f"iMotio, que usa a câmera do computador para corrigir postura e contar repetições "
            f"em tempo real — sem wearables.\n\n"
            f"Vi que vocês têm um espaço incrível em {lead.city} e acredito que essa solução "
            f"poderia ser um diferencial poderoso para o ambiente de vocês.\n\n"
            f"Tenho 15 minutos para uma demonstração rápida essa semana?\n\n"
            f"Eduardo\niMotio | IA para Movimento Humano\n\n"
            f"---\nPara não receber mais e-mails, responda com 'remover'."
        )

    async def send_email(self, lead: Lead, is_followup: bool = False) -> bool:
        """Envia e-mail para um lead, respeitando rate limits."""
        if not self._settings.has_smtp:
            logger.error("SMTP não configurado. Configure SMTP_USER e SMTP_PASSWORD no .env.")
            return False

        if not lead.email:
            logger.warning(f"Lead '{lead.name}' sem e-mail. Pulando.")
            return False

        if not self._rate_limit_ok():
            logger.warning(f"Rate limit atingido ({self._max_per_hour}/hora). Aguardando...")
            return False

        # Gera corpo do e-mail
        raw_message = await self.generate_message_with_ai(lead, is_followup)
        lines = raw_message.split("\n", 1)
        subject = (
            lines[0].replace("Assunto:", "").strip()
            if lines[0].startswith("Assunto:")
            else f"iMotio × {lead.name}"
        )
        body_text = lines[1].strip() if len(lines) > 1 else raw_message

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self._settings.smtp_from_name} <{self._settings.smtp_user}>"
        msg["To"] = lead.email

        # Texto simples
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        # Versão HTML básica
        html_body = body_text.replace("\n", "<br>")
        html = f"<html><body style='font-family:sans-serif;'>{html_body}</body></html>"
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            with smtplib.SMTP(self._settings.smtp_host, self._settings.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self._settings.smtp_user, self._settings.smtp_password)
                server.sendmail(self._settings.smtp_user, lead.email, msg.as_string())

            self._sent_this_hour.append(time.time())
            logger.success(f"[Outreach] E-mail enviado para {lead.name} <{lead.email}>")

            # Atualiza status no banco
            if lead.id:
                followup_at = datetime.utcnow() + timedelta(
                    days=cfg("outreach.followup_after_days", 5)
                )
                await self._repo.update_status(lead.id, LeadStatus.CONTATADO)

            await asyncio.sleep(self._min_delay)
            return True

        except Exception as exc:
            logger.error(f"[Outreach] Falha ao enviar e-mail para {lead.email}: {exc}")
            return False

    async def run_followup_campaign(self) -> int:
        """Envia follow-ups automáticos para leads que ainda não responderam."""
        leads = await self._repo.get_pending_followups()
        sent = 0
        for lead in leads:
            if await self.send_email(lead, is_followup=True):
                sent += 1
        logger.info(f"[Follow-up] {sent} follow-ups enviados.")
        return sent
