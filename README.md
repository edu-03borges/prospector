# Prospector iMotio 🎙

> **Máquina de prospecção inteligente para estúdios criativos** — gravação, foto, vídeo, podcast e coworkings criativos.

Automatiza a busca, enriquecimento, scoring e outreach de leads com scraping ético via Playwright/BeautifulSoup, banco SQLite, exportação multi-formato e um **cockpit interativo 100% no terminal**.

---

## 🚀 Instalação Rápida

### 1. Pré-requisitos
- Python 3.11+
- pip ou uv

### 2. Instalar dependências

```bash
# Dentro da pasta prospector/
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Instala o navegador para o scraper (apenas na primeira vez)
playwright install chromium
```

> No Ubuntu/Debian com Python 3.12+, não use `pip install` no ambiente global.
> Se a `.venv` já existir, basta rodar `source .venv/bin/activate`.

### 3. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Edite o .env com suas chaves (ver seção abaixo)
```

---

## ⚙️ Configuração Opcional

| Variável | Obrigatório | Onde obter |
|----------|-------------|------------|
| `HUNTER_API_KEY` | Não (estima e-mail pelo domínio) | [hunter.io](https://hunter.io/users/sign_up) (plano gratuito: 25/mês) |
| `GROQ_API_KEY` | Não (melhora as mensagens de WhatsApp) | [console.groq.com](https://console.groq.com/keys) (gratuito) |

> **Você pode rodar sem nenhuma chave.** A busca funciona 100% via scraper local e o fluxo principal opera só com recursos locais + WhatsApp Web.

---

## 🖥 Uso — Terminal Interativo

### Abrir o cockpit principal

```bash
.venv/bin/prospector
# ou
.venv/bin/prospector terminal
```

O terminal interativo concentra:
- visão geral do pipeline com KPIs
- fila prioritária de leads
- follow-ups pendentes
- busca de novos leads
- atualização de status e notas
- enriquecimento de leads salvos
- campanhas e follow-ups de WhatsApp
- exportação para CSV, Excel e JSON

### Buscar leads de estúdios via subcomando

```bash
# Busca via scraper local
prospector search --city "Tubarão" --state SC --radius 30

# Busca com palavras-chave específicas
prospector search -c "Rio de Janeiro" -s RJ --keywords "studio fitness,podcast"

# Com mais opções
prospector search -c "Curitiba" -s PR -r 40 --max 80 --keywords "estúdio de podcast,produtora de vídeo"
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

### Disparar WhatsApp

```bash
# Preview sem abrir o WhatsApp
prospector wa --min-score 60 --limit 5 --dry-run

# Disparo real via WhatsApp Web
prospector wa --min-score 60 --limit 10

# Follow-ups pendentes
prospector wa --followup --limit 10
```

O texto padrão do WhatsApp usa `outreach.whatsapp_sender_name` e `outreach.whatsapp_sender_company` em [config/config.yaml](/home/desenv06/Documentos/python/prospector/config/config.yaml).

### Blacklist

```bash
prospector blacklist "Studio XYZ" "Tubarão"
```

### Operar tudo sem dashboard web

```bash
# o comando legado agora redireciona para o terminal
prospector dashboard
```

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
│   │   └── terminal.py      # Cockpit interativo via terminal
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
│   │   └── whatsapp.py      # WhatsApp Web + follow-up
│   └── scrapers/
│       └── maps_scraper.py  # Playwright scraper principal
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
2. **Inclua opt-out** nas mensagens e respeite pedidos de remoção.
3. **Não compartilhe dados** com terceiros.
4. **Respeite pedidos de remoção** — use `prospector blacklist "Nome" "Cidade"` imediatamente.
5. **Delay entre envios** — respeite os limites do WhatsApp para reduzir risco de bloqueio.

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
