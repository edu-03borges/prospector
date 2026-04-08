"""Terminal interativo para operar todo o funil de prospecção sem dashboard web."""

from __future__ import annotations

import asyncio
import shlex
from datetime import datetime, timedelta
from typing import Optional

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from prospector.config.settings import cfg
from prospector.core.scoring import classify_lead_priority, score_lead
from prospector.db.database import LeadRepository
from prospector.enrichment.enricher import LeadEnricher
from prospector.export.exporter import export_csv, export_excel, export_json
from prospector.models.lead import Lead, LeadStatus, SearchQuery
from prospector.outreach.whatsapp import _generate_whatsapp_message, WhatsAppEngine
from prospector.scrapers.maps_scraper import MapsScraper

console = Console()


def _run(coro):
    return asyncio.run(coro)


def _status_choices() -> list[str]:
    return [status.value for status in LeadStatus]


def _lead_action_hint(lead: Lead) -> str:
    if lead.status == LeadStatus.BLACKLIST:
        return "Ignorar este lead."
    if lead.status == LeadStatus.CONVERTIDO:
        return "Lead convertido. Registrar observacoes finais."
    if lead.status == LeadStatus.PERDIDO:
        return "Lead perdido. Revisar motivo antes de reativar."
    if lead.status == LeadStatus.CONTATADO and lead.next_followup_at:
        return f"Follow-up agendado para {lead.next_followup_at:%d/%m/%Y %H:%M}."
    if lead.status == LeadStatus.NOVO and lead.phone:
        return "Acao recomendada: disparar campanha de WhatsApp."
    if lead.status == LeadStatus.NOVO and lead.email:
        return "Acao recomendada: qualificar ou abordar manualmente."
    if lead.status == LeadStatus.NOVO and lead.website:
        return "Acao recomendada: rodar enriquecimento neste lead."
    return "Acao recomendada: revisar e completar dados manualmente."


class ProspectorTerminal:
    """Shell operacional para o pipeline comercial."""

    def __init__(self) -> None:
        self._repo = LeadRepository()

    def run(self) -> None:
        self._render_welcome()
        self.render_overview()

        while True:
            try:
                raw = Prompt.ask(
                    "[bold green]prospector[/]",
                    default="help",
                    show_default=False,
                ).strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[bold]Encerrando terminal.[/]")
                return

            if not raw:
                continue

            try:
                should_exit = self._dispatch(raw)
            except Exception as exc:  # pragma: no cover - protecao do shell
                console.print(f"[red]Falha no comando:[/] {exc}")
                should_exit = False

            if should_exit:
                console.print("[bold]Sessao finalizada.[/]")
                return

    def _dispatch(self, raw: str) -> bool:
        parts = shlex.split(raw)
        command = parts[0].lower()
        args = parts[1:]

        aliases = {
            "1": "overview",
            "2": "search",
            "3": "leads",
            "4": "lead",
            "5": "update",
            "6": "enrich",
            "7": "wa",
            "8": "export",
            "9": "blacklist",
            "10": "delete",
            "11": "help",
            "0": "quit",
            "home": "overview",
            "menu": "help",
            "show": "lead",
            "whatsapp": "wa",
            "exit": "quit",
        }
        command = aliases.get(command, command)

        if command == "overview":
            self.render_overview()
        elif command == "search":
            self.command_search()
        elif command == "leads":
            self.command_list_leads()
        elif command == "lead":
            self.command_lead_detail(args)
        elif command == "update":
            self.command_update_lead(args)
        elif command == "enrich":
            self.command_enrich()
        elif command == "wa":
            self.command_whatsapp_campaign()
        elif command == "export":
            self.command_export()
        elif command == "blacklist":
            self.command_blacklist(args)
        elif command == "delete":
            self.command_delete(args)
        elif command == "help":
            self.render_help()
        elif command == "quit":
            return True
        else:
            console.print("[yellow]Comando desconhecido.[/] Digite [bold]help[/] para ver as opcoes.")
        return False

    def _render_welcome(self) -> None:
        body = (
            "[bold cyan]Prospector Terminal[/]\n"
            "Cockpit de prospeccao 100% no terminal.\n\n"
            "[dim]Comandos rapidos:[/] overview, search, leads, lead <id>, update <id>, "
            "enrich, wa, export, blacklist <id>, delete <id>, quit"
        )
        console.print(Panel(body, border_style="cyan"))

    def render_help(self) -> None:
        table = Table(title="Comandos do Terminal", header_style="bold cyan")
        table.add_column("Atalho", width=8)
        table.add_column("Comando", width=14)
        table.add_column("Funcao")
        rows = [
            ("1", "overview", "Mostra KPIs, fila quente e follow-ups pendentes."),
            ("2", "search", "Busca novos leads via scraper local."),
            ("3", "leads", "Lista leads com filtros operacionais."),
            ("4", "lead", "Abre os detalhes de um lead por ID."),
            ("5", "update", "Atualiza status, notas e follow-up."),
            ("6", "enrich", "Enriquece leads ja salvos e recalcula score."),
            ("7", "wa", "Preview ou disparo de campanha de WhatsApp."),
            ("8", "export", "Exporta a carteira para CSV, Excel ou JSON."),
            ("9", "blacklist", "Bloqueia um lead no funil."),
            ("10", "delete", "Remove um lead do banco."),
            ("11", "help", "Mostra este menu."),
            ("0", "quit", "Encerra a sessao."),
        ]
        for shortcut, command, description in rows:
            table.add_row(shortcut, command, description)
        console.print(table)

    def render_overview(self) -> None:
        snapshot = _run(self._repo.get_pipeline_snapshot())
        counts = _run(self._repo.count_by_status())
        hot_leads = _run(self._repo.get_all(status=LeadStatus.NOVO, min_score=45, limit=8))
        followups = list(_run(self._repo.get_pending_followups()))[:5]

        metrics = Columns(
            [
                Panel.fit(f"[bold]{snapshot['total']}[/]\nTotal", border_style="blue"),
                Panel.fit(f"[bold]{snapshot['avg_score']}[/]\nScore medio", border_style="cyan"),
                Panel.fit(f"[bold]{snapshot['with_email']}[/]\nCom e-mail", border_style="green"),
                Panel.fit(f"[bold]{snapshot['with_phone']}[/]\nCom telefone", border_style="yellow"),
                Panel.fit(f"[bold]{snapshot['hot_new']}[/]\nNovos quentes", border_style="magenta"),
                Panel.fit(f"[bold]{snapshot['followups_due']}[/]\nFollow-ups vencidos", border_style="red"),
            ]
        )
        console.print(metrics)

        status_table = Table(title="Pipeline", header_style="bold blue")
        status_table.add_column("Status")
        status_table.add_column("Qtd.", justify="right")
        for status in LeadStatus:
            status_table.add_row(status.value, str(counts.get(status.value, 0)))
        console.print(status_table)

        if hot_leads:
            table = Table(title="Fila Prioritaria", header_style="bold green")
            table.add_column("ID", width=6)
            table.add_column("Score", width=7)
            table.add_column("Lead", width=32)
            table.add_column("Cidade", width=16)
            table.add_column("Canal", width=16)
            table.add_column("Acao")
            for lead in hot_leads:
                channel = "Email" if lead.email else ("WhatsApp" if lead.phone else "Sem contato")
                table.add_row(
                    str(lead.id or "—"),
                    str(lead.score),
                    lead.display_name[:32],
                    lead.city or "—",
                    channel,
                    _lead_action_hint(lead),
                )
            console.print(table)

        if followups:
            table = Table(title="Follow-ups Pendentes", header_style="bold yellow")
            table.add_column("ID", width=6)
            table.add_column("Lead", width=32)
            table.add_column("Ultimo contato", width=18)
            table.add_column("Proximo follow-up", width=18)
            table.add_column("Ja enviados", width=13)
            for lead in followups:
                table.add_row(
                    str(lead.id or "—"),
                    lead.display_name[:32],
                    lead.last_contacted_at.strftime("%d/%m/%Y") if lead.last_contacted_at else "—",
                    lead.next_followup_at.strftime("%d/%m/%Y") if lead.next_followup_at else "—",
                    str(lead.followup_count),
                )
            console.print(table)

    def command_search(self) -> None:
        default_city = cfg("search.default_city", "Tubarão")
        default_state = cfg("search.default_state", "SC")
        default_radius = float(cfg("search.default_radius_km", 30))
        default_keywords = ",".join(cfg("search.default_keywords", []))
        default_max = int(cfg("scraping.max_results_per_query", 60))

        city = Prompt.ask("Cidade", default=default_city)
        state = Prompt.ask("Estado", default=default_state)
        radius = FloatPrompt.ask("Raio em km", default=default_radius)
        keywords = Prompt.ask("Palavras-chave separadas por virgula", default=default_keywords)
        max_results = IntPrompt.ask("Maximo de resultados", default=default_max)
        enrich = Confirm.ask("Enriquecer dados apos a busca?", default=True)

        created, updated = _run(
            self._search_flow(city, state, radius, keywords, max_results, enrich)
        )
        console.print(
            f"[bold green]Busca concluida.[/] fonte=maps_scraper | +{created} novos | "
            f"~{updated} atualizados"
        )

    async def _search_flow(
        self,
        city: str,
        state: str,
        radius: float,
        keywords_str: str,
        max_results: int,
        enrich: bool,
    ) -> tuple[int, int]:
        query = SearchQuery(
            keywords=[item.strip() for item in keywords_str.split(",") if item.strip()],
            city=city,
            state=state,
            radius_km=radius,
            max_results=max_results,
        )
        searcher = MapsScraper()
        leads_buffer: list[Lead] = []

        async for lead in searcher.search(query):
            if not lead.city:
                lead.city = city
            if not lead.state:
                lead.state = state
            lead.score = score_lead(lead, target_city=city)
            leads_buffer.append(lead)

        if enrich and leads_buffer:
            async with LeadEnricher() as enricher:
                for index, lead in enumerate(leads_buffer):
                    enriched = await enricher.enrich(lead)
                    enriched.score = score_lead(enriched, target_city=city)
                    leads_buffer[index] = enriched

        created = 0
        updated = 0
        for lead in leads_buffer:
            _, is_new = await self._repo.upsert(lead)
            if is_new:
                created += 1
            else:
                updated += 1
        return created, updated

    def command_list_leads(self) -> None:
        raw_status = Prompt.ask("Status", default="todos")
        status = None if raw_status.lower() in {"", "todos", "all"} else LeadStatus(raw_status.lower())
        city = Prompt.ask("Cidade (vazio para todas)", default="")
        query_text = Prompt.ask("Busca textual (nome/cidade/site/e-mail)", default="")
        min_score = IntPrompt.ask("Score minimo", default=0)
        limit = IntPrompt.ask("Limite", default=20)
        email_filter = Prompt.ask("Filtrar por e-mail? (todos/sim/nao)", default="todos")
        phone_filter = Prompt.ask("Filtrar por telefone? (todos/sim/nao)", default="todos")

        has_email = None if email_filter == "todos" else email_filter == "sim"
        has_phone = None if phone_filter == "todos" else phone_filter == "sim"
        leads = _run(
            self._repo.get_all(
                status=status,
                city=city or None,
                min_score=min_score,
                limit=limit,
                query_text=query_text or None,
                has_email=has_email,
                has_phone=has_phone,
            )
        )
        if not leads:
            console.print("[yellow]Nenhum lead encontrado com esses filtros.[/]")
            return

        table = Table(title=f"{len(leads)} lead(s)", header_style="bold magenta")
        table.add_column("ID", width=5)
        table.add_column("Score", width=7)
        table.add_column("Prioridade", width=12)
        table.add_column("Nome", width=32)
        table.add_column("Cidade", width=16)
        table.add_column("Contato", width=22)
        table.add_column("Status", width=12)
        for lead in leads:
            contact = lead.email or lead.phone or "—"
            table.add_row(
                str(lead.id or "—"),
                str(lead.score),
                classify_lead_priority(lead.score),
                lead.display_name[:32],
                lead.city or "—",
                contact[:22],
                lead.status.value,
            )
        console.print(table)

    def command_lead_detail(self, args: list[str]) -> None:
        lead_id = int(args[0]) if args else IntPrompt.ask("ID do lead")
        lead = _run(self._repo.get_by_id(lead_id))
        if not lead:
            console.print(f"[yellow]Lead #{lead_id} nao encontrado.[/]")
            return

        summary = Table.grid(padding=(0, 2))
        summary.add_row("Lead", lead.display_name)
        summary.add_row("Status", lead.status.value)
        summary.add_row("Score", f"{lead.score} ({classify_lead_priority(lead.score)})")
        summary.add_row("Tipo", lead.studio_type.value)
        summary.add_row("Cidade", f"{lead.city or '—'}/{lead.state or '—'}")
        summary.add_row("Endereco", lead.address or "—")
        summary.add_row("Telefone", lead.phone or "—")
        summary.add_row("E-mail", lead.email or "—")
        summary.add_row("Website", lead.website or "—")
        summary.add_row("Instagram", lead.social.instagram or "—")
        summary.add_row("Google Maps", lead.google_maps_url or "—")
        summary.add_row("Avaliacao", str(lead.rating or "—"))
        summary.add_row("Reviews", str(lead.review_count or 0))
        summary.add_row("Ultimo contato", lead.last_contacted_at.strftime("%d/%m/%Y %H:%M") if lead.last_contacted_at else "—")
        summary.add_row("Proximo follow-up", lead.next_followup_at.strftime("%d/%m/%Y %H:%M") if lead.next_followup_at else "—")
        summary.add_row("Acao sugerida", _lead_action_hint(lead))

        notes = lead.notes or "Sem notas registradas."
        console.print(Panel(summary, title=f"Lead #{lead.id}", border_style="cyan"))
        console.print(Panel(notes, title="Notas", border_style="blue"))

    def command_update_lead(self, args: list[str]) -> None:
        lead_id = int(args[0]) if args else IntPrompt.ask("ID do lead")
        lead = _run(self._repo.get_by_id(lead_id))
        if not lead:
            console.print(f"[yellow]Lead #{lead_id} nao encontrado.[/]")
            return

        current_status = lead.status.value
        new_status = Prompt.ask("Novo status", choices=_status_choices(), default=current_status)
        notes = Prompt.ask("Notas", default=lead.notes or "")
        followup_raw = Prompt.ask(
            "Agendar follow-up em quantos dias? (vazio mantém o atual)",
            default="",
        )

        if new_status == LeadStatus.CONTATADO.value and current_status != LeadStatus.CONTATADO.value:
            next_followup = lead.next_followup_at
            if followup_raw.strip():
                next_followup = datetime.utcnow() + timedelta(days=int(followup_raw))
            elif next_followup is None:
                next_followup = datetime.utcnow() + timedelta(days=int(cfg("outreach.followup_after_days", 5)))
            _run(self._repo.mark_contacted(lead_id, notes=notes, next_followup_at=next_followup))
        else:
            clear_followup = new_status in {LeadStatus.CONVERTIDO.value, LeadStatus.PERDIDO.value, LeadStatus.BLACKLIST.value}
            next_followup = None
            if followup_raw.strip():
                next_followup = datetime.utcnow() + timedelta(days=int(followup_raw))
                clear_followup = False
            _run(
                self._repo.update_lead(
                    lead_id,
                    status=LeadStatus(new_status),
                    notes=notes,
                    next_followup_at=next_followup,
                    clear_next_followup=clear_followup,
                )
            )
        console.print(f"[bold green]Lead #{lead_id} atualizado.[/]")

    def command_enrich(self) -> None:
        status_raw = Prompt.ask("Status alvo", default="novo")
        min_score = IntPrompt.ask("Score minimo", default=0)
        limit = IntPrompt.ask("Limite de leads", default=20)
        only_missing_email = Confirm.ask("Somente leads sem e-mail?", default=True)

        updated, gained_email, gained_instagram = _run(
            self._enrich_existing_flow(
                LeadStatus(status_raw),
                min_score,
                limit,
                only_missing_email,
            )
        )
        console.print(
            f"[bold green]Enriquecimento concluido.[/] {updated} atualizados | "
            f"{gained_email} com novo e-mail | {gained_instagram} com novo Instagram"
        )

    async def _enrich_existing_flow(
        self,
        status: LeadStatus,
        min_score: int,
        limit: int,
        only_missing_email: bool,
    ) -> tuple[int, int, int]:
        leads = await self._repo.get_all(
            status=status,
            min_score=min_score,
            limit=limit,
            has_email=False if only_missing_email else None,
        )
        candidates = [lead for lead in leads if lead.website]
        if not candidates:
            return (0, 0, 0)

        updated = 0
        gained_email = 0
        gained_instagram = 0

        async with LeadEnricher() as enricher:
            for lead in candidates:
                had_email = bool(lead.email)
                had_instagram = bool(lead.social.instagram)
                enriched = await enricher.enrich(lead)
                enriched.score = score_lead(enriched, target_city=enriched.city)
                await self._repo.upsert(enriched)
                updated += 1
                if enriched.email and not had_email:
                    gained_email += 1
                if enriched.social.instagram and not had_instagram:
                    gained_instagram += 1
        return updated, gained_email, gained_instagram

    def command_whatsapp_campaign(self) -> None:
        mode = Prompt.ask("Modo", choices=["preview", "send", "followup", "links"], default="preview")
        min_score = IntPrompt.ask("Score minimo", default=50)
        limit = IntPrompt.ask("Limite", default=10)
        custom_message = Prompt.ask("Mensagem customizada (vazio usa IA/template)", default="")

        if mode == "links":
            generated = _run(self._wa_links_flow(min_score, limit, custom_message or None))
            console.print(f"[bold green]{generated} link(s) gerados.[/]")
            return

        is_followup = mode == "followup"
        dry_run = mode == "preview"
        sent, failed = _run(
            self._send_whatsapp_flow(
                min_score=min_score,
                limit=limit,
                dry_run=dry_run,
                is_followup=is_followup,
                custom_message=custom_message or None,
            )
        )
        if dry_run:
            console.print("[bold green]Preview de WhatsApp concluido.[/]")
        else:
            console.print(f"[bold green]Campanha WhatsApp concluida.[/] {sent} enviados | {failed} falhas")

    async def _send_whatsapp_flow(
        self,
        *,
        min_score: int,
        limit: int,
        dry_run: bool,
        is_followup: bool,
        custom_message: Optional[str],
    ) -> tuple[int, int]:
        engine = WhatsAppEngine()
        if is_followup:
            leads = list(await self._repo.get_pending_followups())
            leads = [lead for lead in leads if lead.phone][:limit]
        else:
            leads = await self._repo.get_all(status=LeadStatus.NOVO, min_score=min_score, limit=limit)
            leads = [lead for lead in leads if lead.phone]
        leads = [lead for lead in leads if lead.status != LeadStatus.BLACKLIST]

        if not leads:
            console.print("[yellow]Nenhum lead com telefone encontrado.[/]")
            return (0, 0)

        sent = 0
        failed = 0
        for lead in leads:
            message = custom_message or await _generate_whatsapp_message(lead, is_followup)
            if dry_run:
                console.print(Panel(message, title=f"Preview WhatsApp | {lead.display_name}"))
                continue
            ok = await engine.send_to_lead(lead, is_followup, custom_message)
            if ok:
                sent += 1
            else:
                failed += 1
        return sent, failed

    async def _wa_links_flow(self, min_score: int, limit: int, custom_message: Optional[str]) -> int:
        leads = await self._repo.get_all(status=LeadStatus.NOVO, min_score=min_score, limit=limit)
        leads_with_phone = [lead for lead in leads if lead.phone]
        leads_with_phone = [lead for lead in leads_with_phone if lead.status != LeadStatus.BLACKLIST]
        if not leads_with_phone:
            console.print("[yellow]Nenhum lead com telefone encontrado.[/]")
            return 0

        engine = WhatsAppEngine()
        links = engine.generate_wa_links(leads_with_phone, custom_message)
        table = Table(title="Links WhatsApp", header_style="bold green")
        table.add_column("ID", width=5)
        table.add_column("Lead", width=30)
        table.add_column("Telefone", width=16)
        table.add_column("Link", width=60)
        for lead, item in zip(leads_with_phone, links):
            table.add_row(
                str(lead.id or "—"),
                lead.display_name[:30],
                item["telefone"],
                item["link_whatsapp"][:60],
            )
        console.print(table)
        return len(links)

    def command_export(self) -> None:
        fmt = Prompt.ask("Formato", choices=["csv", "excel", "json"], default="csv")
        status_raw = Prompt.ask("Status (vazio para todos)", default="")
        min_score = IntPrompt.ask("Score minimo", default=0)
        output = Prompt.ask("Nome do arquivo (vazio para automatico)", default="")

        status = LeadStatus(status_raw) if status_raw else None
        leads = _run(self._repo.get_all(status=status, min_score=min_score, limit=10000))
        if not leads:
            console.print("[yellow]Nenhum lead para exportar.[/]")
            return

        if fmt == "excel":
            path = export_excel(leads, output or None)
        elif fmt == "json":
            path = export_json(leads, output or None)
        else:
            path = export_csv(leads, output or None)
        console.print(f"[bold green]Exportado:[/] {path}")

    def command_blacklist(self, args: list[str]) -> None:
        lead_id = int(args[0]) if args else IntPrompt.ask("ID do lead")
        lead = _run(self._repo.get_by_id(lead_id))
        if not lead:
            console.print(f"[yellow]Lead #{lead_id} nao encontrado.[/]")
            return
        _run(
            self._repo.update_lead(
                lead_id,
                status=LeadStatus.BLACKLIST,
                notes=lead.notes,
                clear_next_followup=True,
            )
        )
        console.print(f"[bold red]Lead #{lead_id} enviado para blacklist.[/]")

    def command_delete(self, args: list[str]) -> None:
        lead_id = int(args[0]) if args else IntPrompt.ask("ID do lead")
        lead = _run(self._repo.get_by_id(lead_id))
        if not lead:
            console.print(f"[yellow]Lead #{lead_id} nao encontrado.[/]")
            return
        if not Confirm.ask(f"Remover '{lead.display_name}' permanentemente?", default=False):
            console.print("[dim]Remocao cancelada.[/]")
            return
        deleted = _run(self._repo.delete(lead_id))
        if deleted:
            console.print(f"[bold green]Lead #{lead_id} removido.[/]")
        else:
            console.print(f"[yellow]Lead #{lead_id} nao encontrado.[/]")


def run_terminal() -> None:
    ProspectorTerminal().run()
