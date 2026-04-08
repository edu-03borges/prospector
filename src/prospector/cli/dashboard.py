"""Dashboard Streamlit — pipeline visual de leads com mapa e gráficos."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

# ── Config da página ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Prospector iMotio",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)



# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_async(coro):
    import sys
    import asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    try:
        # Se já existe um loop rodando na thread atual (ex: thread do streamlit)
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Estamos numa thread que já tem um loop (pode acontecer com alguns watchers)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        # Caminho seguro: cria um novo loop isolado para essa execução e destrói no fim
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
            asyncio.set_event_loop(None)


@st.cache_data(ttl=30)
def load_leads(status_filter: str | None = None, min_score: int = 0) -> pd.DataFrame:
    from prospector.db.database import LeadRepository
    from prospector.models.lead import LeadStatus

    repo = LeadRepository()
    status = LeadStatus(status_filter) if status_filter else None
    leads = _run_async(repo.get_all(status=status, min_score=min_score, limit=5000))

    if not leads:
        return pd.DataFrame()

    rows = []
    for lead in leads:
        rows.append({
            "id": lead.id,
            "nome": lead.display_name,
            "tipo": lead.studio_type.value,
            "cidade": lead.city or "—",
            "estado": lead.state or "—",
            "telefone": lead.phone or "",
            "whatsapp": lead.whatsapp_link or "",
            "email": lead.email or "",
            "score": lead.score,
            "status": lead.status.value,
            "avaliacao": lead.rating or 0,
            "avaliacoes_count": lead.review_count or 0,
            "instagram": lead.social.instagram or "",
            "site": lead.website or "",
            "lat": lead.latitude,
            "lon": lead.longitude,
            "criado_em": lead.created_at,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["prioridade"] = df["score"].apply(
            lambda s: "Alta" if s >= 75 else ("Média" if s >= 45 else "Baixa")
        )
    return df


@st.cache_data(ttl=30)
def load_counts() -> dict[str, int]:
    from prospector.db.database import LeadRepository
    repo = LeadRepository()
    return _run_async(repo.count_by_status())


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    # Usando colunas nativas do Streamlit para centralizar e diminuir a logo na sidebar
    col_l1, col_l2, col_l3 = st.columns([1, 4, 1])
    with col_l2:
        st.image(r"C:\Users\eduar\Documents\iMotio\app-imotio\static\logo-elarin-green-white.png", use_container_width=True)
    st.markdown("---")

    st.subheader(":material/search: Buscar Leads")
    with st.form("search_form"):
        s_keywords = st.text_input("O que buscar? (ex: studio fitness)", value="studio fitness")
        s_city = st.text_input("Cidade", value="Tubarão")
        s_state = st.text_input("Estado", value="SC")
        s_radius = st.slider("Raio (km)", 5, 100, 30)
        s_max = st.slider("Máx. resultados", 10, 200, 60)
        s_enrich = st.checkbox("Enriquecer e-mails", value=True)
        s_submit = st.form_submit_button("Iniciar Busca", type="primary", use_container_width=True)

    if s_submit:
        from prospector.cli.main import _search_flow
        with st.spinner(f"Buscando '{s_keywords}' em {s_city}/{s_state}…"):
            _run_async(_search_flow(s_city, s_state, s_radius, s_keywords, s_max, s_enrich, silent=True))
        st.cache_data.clear()
        st.success("Busca concluída!")
        st.rerun()

    st.markdown("---")
    st.subheader(":material/tune: Filtros da Tabela")
    status_opts = ["todos", "novo", "contatado", "qualificado", "convertido", "perdido"]
    f_status = st.selectbox("Status", status_opts)
    f_min_score = st.slider("Score mínimo", 0, 100, 0)
    
    st.markdown("---")
    st.subheader(":material/delete: Lixeira")
    with st.form("delete_form"):
        del_id = st.text_input("ID do Lead para remover (veja na tabela)", placeholder="Ex: 5")
        del_submit = st.form_submit_button("Remover Lead Permanentemente", type="secondary", use_container_width=True)
    
    if del_submit and del_id.isdigit():
        from prospector.db.database import LeadRepository
        repo = LeadRepository()
        # O repositório não tem metodo delete fácil exposto direto, mas tem delete via sqlalchemy
        # Vamos fazer um pequeno wrapper ou puxar o model e deletar
        async def _delete_lead(lead_id: int):
            from sqlalchemy import delete
            from prospector.db.database import LeadORM, get_session
            async with await get_session() as session:
                await session.execute(delete(LeadORM).where(LeadORM.id == lead_id))
                await session.commit()
        
        _run_async(_delete_lead(int(del_id)))
        st.cache_data.clear()
        st.success(f"Lead #{del_id} removido com sucesso!")
        st.rerun()
    elif del_submit:
        st.warning("⚠️ Digite um ID numérico válido.")

    st.markdown("---")
    st.subheader(":material/smart_toy: Automação WhatsApp")
    with st.expander("Robô de Disparo", expanded=False):
        st.caption("O robô vai abrir um nevagador invisível. Na 1ª vez, você precisará escanear o QR Code que vai pular na tela.")
        wa_limit = st.slider("Máximo de envios agora", 1, 30, 5)
        wa_min_score = st.slider("Exigir Score mínimo", 0, 100, 30)
        
        if st.button("Disparar WhatsApp", use_container_width=True):
            from prospector.outreach.whatsapp import WhatsAppEngine
            with st.spinner("Iniciando motor do WhatsApp... Fique de olho numa janela nova do Chrome."):
                engine = WhatsAppEngine()
                sent, failed = _run_async(engine.run_campaign(min_score=wa_min_score, limit=wa_limit))
            st.success(f"Finalizado! {sent} enviados | {failed} falhas/pulados.")
            st.cache_data.clear()
            st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────

st.header("iMotio Prospector")
st.caption("Pipeline de prospecção B2B")

counts = load_counts()
total = sum(counts.values())

# KPIs
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(":material/database: Total", total)
c2.metric(":material/fiber_new: Novos", counts.get("novo", 0))
c3.metric(":material/mail: Contatados", counts.get("contatado", 0))
c4.metric(":material/star: Qualificados", counts.get("qualificado", 0))
c5.metric(":material/monetization_on: Convertidos", counts.get("convertido", 0))

st.markdown("---")

# Carrega dados
df = load_leads(
    status_filter=f_status if f_status != "todos" else None,
    min_score=f_min_score,
)

if df.empty:
    st.info("Nenhum lead encontrado. Use o painel lateral para iniciar uma busca!")
    st.stop()

# ── Gráficos ──────────────────────────────────────────────────────────────────

tab1, tab2, tab3 = st.tabs([":material/list: Lista de Leads", ":material/pie_chart: Análises", ":material/map: Mapa"])

with tab1:
    cols_display = ["id", "nome", "tipo", "cidade", "telefone", "email", "score", "prioridade", "status", "whatsapp"]
    st.dataframe(
        df[[c for c in cols_display if c in df.columns]],
        use_container_width=True,
        height=420,
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100),
            "whatsapp": st.column_config.LinkColumn("WhatsApp"),
        },
    )

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button(":material/download: Exportar CSV", use_container_width=True):
            from prospector.db.database import LeadRepository
            from prospector.export.exporter import export_csv
            leads = _run_async(LeadRepository().get_all(limit=10000))
            path = export_csv(leads)
            st.success(f"CSV salvo em: {path}")

    with col_b:
        if st.button(":material/table_chart: Exportar Excel", use_container_width=True):
            from prospector.db.database import LeadRepository
            from prospector.export.exporter import export_excel
            leads = _run_async(LeadRepository().get_all(limit=10000))
            path = export_excel(leads)
            st.success(f"Excel salvo em: {path}")


with tab2:
    col1, col2 = st.columns(2)

    with col1:
        fig_status = px.pie(
            names=list(counts.keys()),
            values=list(counts.values()),
            title="Distribuição por Status",
            color_discrete_sequence=px.colors.qualitative.Bold,
            hole=0.4,
        )
        fig_status.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0",
        )
        st.plotly_chart(fig_status, use_container_width=True)

    with col2:
        por_tipo = df.groupby("tipo").size().reset_index(name="count")
        fig_tipo = px.bar(
            por_tipo, x="tipo", y="count",
            title="Leads por Tipo de Estúdio",
            color="count",
            color_continuous_scale="Purples",
        )
        fig_tipo.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0",
            showlegend=False,
        )
        st.plotly_chart(fig_tipo, use_container_width=True)

    # Score distribution
    fig_score = px.histogram(
        df, x="score", nbins=20,
        title="Distribuição de Scores",
        color_discrete_sequence=["#74c611"],
    )
    fig_score.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e2e8f0",
    )
    st.plotly_chart(fig_score, use_container_width=True)


with tab3:
    geo_df = df[df["lat"].notna() & df["lon"].notna()].copy()
    if geo_df.empty:
        st.info("Nenhum lead com coordenadas GPS disponíveis para o mapa.")
    else:
        fig_map = px.scatter_mapbox(
            geo_df,
            lat="lat", lon="lon",
            hover_name="nome",
            hover_data=["cidade", "score", "email", "telefone"],
            color="score",
            color_continuous_scale="Viridis",
            size="score",
            size_max=18,
            zoom=10,
            title="Mapa de Leads",
            mapbox_style="carto-darkmatter",
        )
        fig_map.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0",
            margin={"r": 0, "t": 40, "l": 0, "b": 0},
        )
        st.plotly_chart(fig_map, use_container_width=True)
