"""CLI principal do Prospector iMotio via Typer."""

from __future__ import annotations

import asyncio
import sys
import io

# Força UTF-8 no Windows para suporte a emojis
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
from typing import Optional

import typer
from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from typer import Context
from pathlib import Path

from prospector.cli.terminal import run_terminal
from prospector.config.settings import DATA_DIR, get_settings
from prospector.core.scoring import classify_lead_priority, score_lead
from prospector.db.database import LeadRepository
from prospector.enrichment.enricher import LeadEnricher
from prospector.export.exporter import export_csv, export_excel, export_json
from prospector.models.lead import Lead, LeadStatus, SearchQuery
from prospector.outreach.whatsapp import WhatsAppEngine

app = typer.Typer(
    name="prospector",
    help="🎙 Prospector iMotio — máquina de leads para estúdios criativos.",
    rich_markup_mode="markdown",
    add_completion=False,
)
console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup_logger(debug: bool = False) -> None:
    import sys
    logger.remove()
    level = "DEBUG" if debug else get_settings().log_level
    logger.add(sys.stderr, level=level, colorize=True, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")


def _run(coro):
    """Executa corrotina no loop de eventos."""
    try:
        return asyncio.run(coro)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[bold red]Falha no comando:[/] {exc}")
        raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def app_callback(
    ctx: Context,
    debug: bool = typer.Option(False, "--debug", help="Modo verboso"),
) -> None:
    """Abre o terminal interativo quando nenhum subcomando for informado."""
    _setup_logger(debug)
    if ctx.invoked_subcommand is None:
        run_terminal()


# ── Comando: search ───────────────────────────────────────────────────────────

@app.command("search", help="🔍 Busca novos estúdios e salva no banco de dados.")
def cmd_search(
    city: str = typer.Option("Tubarão", "--city", "-c", help="Cidade alvo da busca"),
    state: str = typer.Option("SC", "--state", "-s", help="UF (ex: SC, RJ, MG)"),
    radius: float = typer.Option(30.0, "--radius", "-r", help="Raio em km"),
    keywords: Optional[str] = typer.Option(None, "--keywords", "-k", help="Palavras-chave extras (separadas por vírgula)"),
    max_results: int = typer.Option(60, "--max", "-m", help="Máximo de resultados por busca"),
    enrich: bool = typer.Option(True, "--enrich/--no-enrich", help="Enriquecer com e-mail e redes sociais"),
    debug: bool = typer.Option(False, "--debug", help="Modo verboso"),
) -> None:
    _setup_logger(debug)
    _run(_search_flow(city, state, radius, keywords, max_results, enrich))


async def _search_flow(city, state, radius, keywords_str, max_results, enrich, silent=False) -> None:
    repo = LeadRepository()

    kw_list = [k.strip() for k in keywords_str.split(",")] if keywords_str else []
    query = SearchQuery(
        keywords=kw_list,
        city=city,
        state=state,
        radius_km=radius,
        max_results=max_results,
    )

    from prospector.scrapers.maps_scraper import MapsScraper

    searcher = MapsScraper()
    source_used = "maps_scraper"

    if not silent:
        console.print(f"[bold cyan]⚡[/] Fonte de busca: [bold]{source_used}[/]")

    created_count = 0
    updated_count = 0
    leads_buffer: list[Lead] = []

    if silent:
        # Pula a interface Progress bar do Rich para não quebrar no Streamlit
        async for lead in searcher.search(query):
            lead.city = lead.city or city
            lead.state = lead.state or state
            lead.score = score_lead(lead, target_city=city)
            leads_buffer.append(lead)

        if enrich and leads_buffer:
            async with LeadEnricher() as enricher:
                for i, lead in enumerate(leads_buffer):
                    leads_buffer[i] = await enricher.enrich(lead)
                    leads_buffer[i].score = score_lead(leads_buffer[i], target_city=city)

        for lead in leads_buffer:
            _, is_new = await repo.upsert(lead)
            if is_new:
                created_count += 1
            else:
                updated_count += 1
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Buscando estúdios em {city}/{state}…", total=max_results)

        async for lead in searcher.search(query):
            lead.city = lead.city or city
            lead.state = lead.state or state
            # Score inicial
            lead.score = score_lead(lead, target_city=city)
            leads_buffer.append(lead)
            progress.advance(task)

        progress.update(task, description="Enriquecendo leads…")

        if enrich and leads_buffer:
            async with LeadEnricher() as enricher:
                for i, lead in enumerate(leads_buffer):
                    leads_buffer[i] = await enricher.enrich(lead)
                    # Recalcula score após enriquecimento
                    leads_buffer[i].score = score_lead(leads_buffer[i], target_city=city)
                    progress.advance(task, advance=0)

        progress.update(task, description="Salvando no banco…")

        for lead in leads_buffer:
            _, is_new = await repo.upsert(lead)
            if is_new:
                created_count += 1
            else:
                updated_count += 1

    console.print(
        f"\n[bold]✅ Busca concluída![/] "
        f"[green]+{created_count} novos[/] | [cyan]~{updated_count} atualizados[/]"
    )


# ── Comando: list ─────────────────────────────────────────────────────────────

@app.command("list", help="📋 Lista leads do banco de dados.")
def cmd_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filtrar por status: novo, contatado, etc."),
    city: Optional[str] = typer.Option(None, "--city", "-c", help="Filtrar por cidade"),
    min_score: int = typer.Option(0, "--min-score", help="Score mínimo"),
    limit: int = typer.Option(50, "--limit", "-l", help="Máximo de linhas"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    _setup_logger(debug)
    _run(_list_flow(status, city, min_score, limit))


async def _list_flow(status_str, city, min_score, limit) -> None:
    repo = LeadRepository()
    status = LeadStatus(status_str) if status_str else None
    leads = await repo.get_all(status=status, city=city, min_score=min_score, limit=limit)

    if not leads:
        console.print("[yellow]Nenhum lead encontrado com os filtros aplicados.[/]")
        return

    table = Table(
        show_header=True, header_style="bold magenta",
        title=f"🎙 {len(leads)} lead(s) encontrados",
    )
    table.add_column("ID", style="dim", width=5)
    table.add_column("Score", width=7)
    table.add_column("Prioridade", width=12)
    table.add_column("Nome", width=30)
    table.add_column("Cidade", width=15)
    table.add_column("Telefone", width=16)
    table.add_column("E-mail", width=30)
    table.add_column("Status", width=12)

    for lead in leads:
        priority = classify_lead_priority(lead.score)
        table.add_row(
            str(lead.id or "—"),
            f"[bold]{lead.score}[/]",
            priority,
            lead.display_name[:30],
            lead.city or "—",
            lead.phone or "—",
            lead.email or "[dim]sem e-mail[/dim]",
            _status_badge(lead.status),
        )

    console.print(table)


def _status_badge(status: LeadStatus) -> str:
    colors = {
        LeadStatus.NOVO: "blue",
        LeadStatus.CONTATADO: "yellow",
        LeadStatus.QUALIFICADO: "cyan",
        LeadStatus.CONVERTIDO: "green",
        LeadStatus.PERDIDO: "red",
        LeadStatus.BLACKLIST: "dim",
    }
    color = colors.get(status, "white")
    return f"[{color}]{status.value}[/{color}]"


# ── Comando: export ───────────────────────────────────────────────────────────

@app.command("export", help="📤 Exporta leads para CSV, Excel ou JSON.")
def cmd_export(
    format: str = typer.Option("csv", "--format", "-f", help="csv | excel | json"),
    status: Optional[str] = typer.Option(None, "--status", help="Filtrar por status"),
    min_score: int = typer.Option(0, "--min-score"),
    filename: Optional[str] = typer.Option(None, "--output", "-o"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    _setup_logger(debug)
    _run(_export_flow(format, status, min_score, filename))


async def _export_flow(format_str, status_str, min_score, filename) -> None:
    repo = LeadRepository()
    status = LeadStatus(status_str) if status_str else None
    leads = await repo.get_all(status=status, min_score=min_score, limit=10000)

    if not leads:
        console.print("[yellow]Nenhum lead para exportar.[/]")
        return

    if format_str == "excel":
        path = export_excel(leads, filename)
    elif format_str == "json":
        path = export_json(leads, filename)
    else:
        path = export_csv(leads, filename)

    console.print(f"[bold green]✅ Exportado:[/] {path}")


# ── Comando: status ───────────────────────────────────────────────────────────

@app.command("status", help="📊 Exibe resumo do pipeline de leads.")
def cmd_status(debug: bool = typer.Option(False, "--debug")) -> None:
    _setup_logger(debug)
    _run(_status_flow())


async def _status_flow() -> None:
    repo = LeadRepository()
    counts = await repo.count_by_status()

    total = sum(counts.values())
    table = Table(title="📊 Pipeline de Leads", header_style="bold blue")
    table.add_column("Status")
    table.add_column("Quantidade", justify="right")
    table.add_column("% do Total", justify="right")

    for status in LeadStatus:
        count = counts.get(status.value, 0)
        pct = f"{count / total * 100:.1f}%" if total else "0%"
        table.add_row(_status_badge(status), str(count), pct)

    table.add_section()
    table.add_row("[bold]TOTAL[/]", f"[bold]{total}[/]", "100%")
    console.print(table)


# ── Comando: blacklist ────────────────────────────────────────────────────────

@app.command("blacklist", help="🚫 Adiciona um lead à blacklist.")
def cmd_blacklist(
    name: str = typer.Argument(..., help="Nome do estúdio"),
    city: str = typer.Argument(..., help="Cidade"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    _setup_logger(debug)
    _run(LeadRepository().add_to_blacklist(name, city))
    console.print(f"[red]🚫 '{name}' adicionado à blacklist.[/]")


# ── Comando: dashboard ────────────────────────────────────────────────────────

@app.command("dashboard", help="🖥 Alias legado para o terminal interativo.")
def cmd_dashboard(debug: bool = typer.Option(False, "--debug")) -> None:
    _setup_logger(debug)
    console.print("[yellow]O dashboard web foi substituído pelo terminal interativo.[/]")
    run_terminal()


@app.command("terminal", help="🧭 Abre o cockpit interativo no terminal.")
def cmd_terminal(debug: bool = typer.Option(False, "--debug")) -> None:
    _setup_logger(debug)
    run_terminal()


# ── Comando: wa (WhatsApp automático) ────────────────────────────────────────

@app.command("wa", help="💬 Envia mensagens via WhatsApp Web.")
def cmd_wa(
    min_score: int = typer.Option(50, "--min-score", help="Score mínimo dos leads"),
    limit: int = typer.Option(10, "--limit", "-l", help="Máximo de mensagens por execução"),
    followup: bool = typer.Option(False, "--followup", help="Enviar follow-ups pendentes"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Mensagem customizada (substitui IA)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra preview sem abrir WhatsApp"),
    status: Optional[str] = typer.Option(None, "--status", help="Filtrar leads por status"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """
    Dispara mensagens de WhatsApp via WhatsApp Web para leads com telefone.

    Use --dry-run para visualizar as mensagens antes de enviar.
    """
    _setup_logger(debug)
    _run(_wa_flow(min_score, limit, followup, message, dry_run, status))


async def _wa_flow(
    min_score: int,
    limit: int,
    is_followup: bool,
    custom_message: Optional[str],
    dry_run: bool,
    status_str: Optional[str],
) -> None:
    engine = WhatsAppEngine()
    repo = LeadRepository()

    # Decide fonte de leads
    if is_followup:
        leads = await repo.get_pending_followups()
        leads = [l for l in leads if l.phone][:limit]
    else:
        status = LeadStatus(status_str) if status_str else LeadStatus.NOVO
        leads = await repo.get_all(status=status, min_score=min_score, limit=limit)
        leads = [l for l in leads if l.phone]
    leads = [lead for lead in leads if lead.status != LeadStatus.BLACKLIST]

    if not leads:
        console.print("[yellow]Nenhum lead com telefone encontrado para os filtros informados.[/]")
        return

    console.print("[bold cyan]⚡[/] Usando [bold]WhatsApp Web[/] via Playwright (browser abre automaticamente)")

    console.print(f"[bold]💬 {len(leads)} lead(s) com telefone selecionados[/] (dry_run={dry_run})\n")

    from prospector.outreach.whatsapp import _generate_whatsapp_message

    for lead in leads:
        msg = custom_message or await _generate_whatsapp_message(lead, is_followup)

        if dry_run:
            console.print(f"[dim]─── Preview: {lead.name} ({lead.phone}) ───[/dim]")
            console.print(f"{msg}\n")
            wa_link = engine.generate_wa_links([lead], msg)[0]["link_whatsapp"]
            console.print(f"[blue]Link:[/] {wa_link[:80]}…\n")
        else:
            console.print(f"  Enviando para [bold]{lead.name}[/] ({lead.phone})…")
            ok = await engine.send_to_lead(lead, is_followup, custom_message)
            icon = "✅" if ok else "❌"
            console.print(f"  {icon}")

    if not dry_run:
        console.print(f"\n[bold green]✅ Campanha WhatsApp concluída![/]")


# ── Comando: wa-links (geração de links manuais) ──────────────────────────────

@app.command("wa-links", help="🔗 Gera links wa.me para disparo manual no celular.")
def cmd_wa_links(
    min_score: int = typer.Option(40, "--min-score", help="Score mínimo"),
    limit: int = typer.Option(30, "--limit", "-l", help="Máximo de links"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Texto da mensagem (padrão se vazio)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Salvar links num arquivo .txt"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """
    Gera uma lista de links wa.me prontos para clicar.
    Ideal para fazer o disparo manualmente pelo celular sem precisar digitar os números.
    Cada link já abre o WhatsApp com a mensagem pré-preenchida.
    """
    _setup_logger(debug)
    _run(_wa_links_flow(min_score, limit, message, output))


async def _wa_links_flow(
    min_score: int,
    limit: int,
    custom_message: Optional[str],
    output_file: Optional[str],
) -> None:
    repo = LeadRepository()
    leads = await repo.get_all(status=LeadStatus.NOVO, min_score=min_score, limit=limit)
    leads_with_phone = [l for l in leads if l.phone]
    leads_with_phone = [lead for lead in leads_with_phone if lead.status != LeadStatus.BLACKLIST]

    if not leads_with_phone:
        console.print("[yellow]Nenhum lead com telefone encontrado.[/]")
        return

    engine = WhatsAppEngine()
    links = engine.generate_wa_links(leads_with_phone, custom_message)

    # Tabela no terminal
    table = Table(
        title=f"🔗 {len(links)} Links WhatsApp Gerados",
        header_style="bold green",
    )
    table.add_column("#", width=4)
    table.add_column("Nome", width=30)
    table.add_column("Cidade", width=15)
    table.add_column("Score", width=7)
    table.add_column("Telefone", width=16)
    table.add_column("Link WA", width=50)

    file_lines = []
    for i, item in enumerate(links, 1):
        table.add_row(
            str(i),
            item["nome"][:30],
            item.get("cidade", ""),
            str(item["score"]),
            item["telefone"],
            f"[link={item['link_whatsapp']}]{item['link_whatsapp'][:45]}…[/link]",
        )
        file_lines.append(
            f"{i}. {item['nome']} | {item['telefone']} | {item['link_whatsapp']}"
        )

    console.print(table)

    # Salva arquivo se solicitado
    if output_file:
        from pathlib import Path
        from prospector.config.settings import DATA_DIR
        out_path = DATA_DIR / "exports" / output_file
        out_path.write_text("\n".join(file_lines), encoding="utf-8")
        console.print(f"\n[bold green]✅ Links salvos em:[/] {out_path}")
    else:
        console.print(
            "\n[dim]Dica: use [bold]--output links.txt[/bold] para salvar os links num arquivo.[/dim]"
        )


if __name__ == "__main__":
    app()
