# Prospector iMotio 🎙

> **Máquina de prospecção inteligente para estúdios criativos** — gravação, foto, vídeo, podcast e coworkings criativos.

Automatiza a busca, enriquecimento, scoring e outreach de leads com **Google Places API**, scraping ético via Playwright/BeautifulSoup, banco SQLite, exportação multi-formato e dashboard Streamlit com mapa.

---

## 🚀 Instalação Rápida

### 1. Pré-requisitos
- Python 3.11+
- pip ou uv

### 2. Instalar dependências

```bash
# Dentro da pasta prospector/
pip install -e ".[dev]"

# Instala o navegador para o scraper (apenas na primeira vez)
playwright install chromium
```

### 3. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Edite o .env com suas chaves (ver seção abaixo)
```

---

## ⚙️ Configuração de APIs

| Variável | Obrigatório | Onde obter |
|----------|-------------|------------|
| `GOOGLE_PLACES_API_KEY` | Não (usa scraper como fallback) | [Google Cloud Console](https://console.cloud.google.com/) → Places API |
| `HUNTER_API_KEY` | Não (estima e-mail pelo domínio) | [hunter.io](https://hunter.io/users/sign_up) (plano gratuito: 25/mês) |
| `GROQ_API_KEY` | Não (usa template padrão) | [console.groq.com](https://console.groq.com/keys) (gratuito) |
| `SMTP_USER` + `SMTP_PASSWORD` | Só para envio de e-mails | Gmail: ative *App Password* em Segurança |

> **Você pode rodar sem nenhuma chave.** O sistema usa scraper ético como fallback para busca e templates embutidos para e-mails.

---

## 🖥 Uso — Linha de Comando

### Buscar leads de estúdios

```bash
# Busca básica (usa scraper se sem API key)
prospector search --city "Tubarão" --state SC --radius 30

# Com mais opções
prospector search -c "Belo Horizonte" -s MG -r 50 --max 100 --keywords "studio fitness,podcast"

# Sem enriquecer (mais rápido)
prospector search -c "Rio de Janeiro" -s RJ --no-enrich
```

### Listar leads salvos

```bash
prospector list
prospector list --status novo --min-score 50 --limit 20
prospector list --city "Tubarão"
```

### Ver resumo do pipeline

```bash
prospector status
```

### Exportar planilha

```bash
prospector export --format csv
prospector export --format excel --min-score 40 --output minha_lista.xlsx
prospector export --format json
```

### Enviar e-mails em massa

```bash
# Preview sem enviar
prospector send --min-score 60 --limit 5 --dry-run

# Enviar de verdade (garanta que SMTP_USER e SMTP_PASSWORD estão no .env)
prospector send --min-score 60 --limit 10
```

### Follow-ups automáticos

```bash
prospector followup
```

### Blacklist

```bash
prospector blacklist "Studio XYZ" "Tubarão"
```

### Dashboard Visual

```bash
prospector dashboard
# Ou diretamente:
streamlit run src/prospector/cli/dashboard.py
```

---

## 📊 Dashboard Streamlit

O dashboard inclui:
- **KPIs** de pipeline (total, novos, contatados, qualificados, convertidos)
- **Tabela** de leads com filtros e links de WhatsApp clicáveis
- **Gráficos** de distribuição por status, tipo de estúdio e score
- **Mapa interativo** com pins georreferenciados
- **Busca integrada** no painel lateral (sem sair do dashboard)

---

## 🏗 Estrutura do Projeto

```
prospector/
├── config/
│   └── config.yaml          # Configuração de busca, scoring e outreach
├── data/
│   ├── prospector.db        # Banco SQLite (gerado automaticamente)
│   └── exports/             # CSVs, Excels e JSONs gerados
├── src/prospector/
│   ├── cli/
│   │   ├── main.py          # CLI Typer (ponto de entrada)
│   │   └── dashboard.py     # Dashboard Streamlit
│   ├── config/
│   │   └── settings.py      # Configurações via .env e config.yaml
│   ├── core/
│   │   └── scoring.py       # Scoring inteligente de leads
│   ├── db/
│   │   └── database.py      # SQLAlchemy async + repositório
│   ├── enrichment/
│   │   └── enricher.py      # E-mail, redes sociais, Hunter.io
│   ├── export/
│   │   └── exporter.py      # CSV, Excel, JSON
│   ├── models/
│   │   └── lead.py          # Modelos Pydantic e enums
│   ├── outreach/
│   │   └── outreach.py      # SMTP + Groq AI + follow-up
│   └── scrapers/
│       ├── google_places.py # Google Places API (New)
│       └── maps_scraper.py  # Playwright scraper (fallback)
└── templates/
    └── email_first_contact.html  # Template de e-mail HTML
```

---

## 📈 Scoring de Leads

O sistema calcula um **score de 0 a 100** para cada lead baseado em critérios comerciais:

| Critério | Pontos |
|----------|--------|
| E-mail real encontrado | +30 |
| Telefone disponível | +20 |
| Site disponível | +15 |
| Instagram encontrado | +10 |
| Avaliação Google ≥ 4.5 | +10 |
| 50+ avaliações no Google | +10 |
| Localização na cidade-alvo | +5 |

**Prioridades:**
- 🔥 Alta: score ≥ 75
- ⚡ Média: score 45-74
- 🧊 Baixa: score < 45

---

## ⚖️ Conformidade Legal (LGPD)

> **IMPORTANTE:** Este software foi desenvolvido respeitando a **Lei Geral de Proteção de Dados (Lei nº 13.709/2018)**.

**Boas práticas obrigatórias:**
1. **Use apenas para pessoa jurídica** — contato B2B com estúdios é legítimo sob LGPD.
2. **Inclua opt-out** em todos os e-mails (já incluso nos templates).
3. **Não compartilhe dados** com terceiros.
4. **Respeite pedidos de remoção** — use `prospector blacklist "Nome" "Cidade"` imediatamente.
5. **Delay entre envios** — o limite de 15 e-mails/hora previne spam e protege sua reputação.

---

## 🔧 Extensões Futuras

- [ ] Scraper de Solutudo, Apontador e ABG Negócios
- [ ] Integração com CRM (HubSpot, Pipedrive via API)
- [ ] Webhook de resposta automática (parser de e-mail)
- [ ] Modo multi-cidade com fila de tarefas (Celery + Redis)
- [ ] Relatório semanal em PDF automatizado

---

## 📄 Licença

MIT © iMotio 2024
