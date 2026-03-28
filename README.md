# PBIChat

AI-powered chat assistant for Power BI with live SQL access to Databricks and SQL Server. Ask questions in natural language, get instant data-driven answers with interactive charts. Supports **three modes**: database mode (SQL against connected warehouses), **inline data mode** (drag columns from Power BI's data model — no database needed), and **schema-only mode** (TMDL files without database connections — AI explains data structure, suggests DAX, writes example SQL).

## Architecture

```
Power BI Desktop / Service
  └── PBIChat Visual (TypeScript/LESS)
        └── HTTPS requests
              └── PBIChat Backend (Python/FastAPI — stateless)
                    ├── LLM (GPT via Azure OpenAI)
                    ├── Database connections (Databricks / SQL Server)
                    └── Local config files (config.json + semantic_model.txt)
```

**Three data modes**:
- **Database mode** (agentic loop): User question → LLM → SQL → Database → Results → LLM → Factual response (up to 5 iterations)
- **Inline data mode**: User drags columns into "Columns" field well → Visual extracts CSV + summary stats from ALL rows → LLM analyzes directly (no SQL, no database needed)
- **Schema-only mode**: TMDL files uploaded, no DB connections → LLM uses semantic model context to explain structure, suggest DAX/SQL, answer data model questions (single call, no SQL execution)

## Project Structure

```
PBIChat/
├── backend/
│   ├── main.py              # FastAPI app — all endpoints, agentic loop
│   ├── config.json          # Database connections and settings (not committed)
│   ├── semantic_model.txt   # TMDL semantic model content (not committed)
│   ├── .env                 # Runtime config (API keys — not committed)
│   ├── .env.example         # Template for .env
│   ├── requirements.txt     # Python dependencies
│   ├── Dockerfile           # Container build for deployment
│   └── aws/
│       ├── deploy.sh        # One-command deploy + auto-scaling setup
│       └── task-definition.json  # ECS Fargate task (1024 CPU, 2048 MB)
├── visual/
│   ├── src/visual.ts        # Main Power BI visual class (TypeScript)
│   ├── style/visual.less    # All visual styling (dark/light themes)
│   ├── pbiviz.json          # Visual metadata and config
│   ├── capabilities.json    # Power BI capabilities declaration
│   ├── tsconfig.json        # TypeScript config
│   └── package.json         # Node dependencies
├── legal/                   # Privacy Policy, EULA, ToS, DPA, Cookie Policy
├── PLAN.md                  # AppSource publication plan
└── README.md
```

## Backend

### Configuration

The backend loads configuration from local files:

| Data | Storage |
|------|---------|
| Database connections | `config.json` |
| Semantic model (TMDL) | `semantic_model.txt` |
| Extra context | `config.json` |
| API keys | `.env` environment variables |

### Tech Stack
- **Python 3.12** with FastAPI + uvicorn
- **httpx** for async HTTP calls to OpenRouter and Databricks
- **pymssql** for SQL Server connectivity
- **pydantic** for request/response validation

#### TMDL Slimming
Large semantic models are automatically optimized before inclusion in the LLM prompt:
- Culture/locale files (e.g. `en-US.tmdl`) are stripped entirely
- Non-essential metadata (lineageTag, annotation, summarizeBy) removed
- Consecutive blank lines collapsed
- Hard truncation at 400K characters
- Typical reduction: 95%+ (e.g. 1.25M → 55K characters)

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check — returns LLM config status |
| GET | `/warehouse-status` | Databricks warehouse state |
| POST | `/chat` | **Main endpoint** — inline data analysis or agentic SQL loop (auto-detected) |
| POST | `/config` | Update config (connections, extra context) |
| GET | `/config` | Read current config |
| GET | `/connections` | List database connections (secrets redacted) |
| POST | `/connections` | Save database connections |
| POST | `/test-connection/{id}` | Test specific connection |
| POST | `/test-connection` | Test first Databricks connection |
| POST | `/upload-tmdl` | Upload .tmdl files → saved to semantic_model.txt |
| POST | `/verify-password` | Verify settings password |

### Environment Variables (`.env`)

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `LLM_MODEL` | Model deployment name on Azure OpenAI |
| `SETTINGS_PASSWORD` | Password for accessing the settings panel |
| `RATE_LIMIT_RPM` | Per-IP requests per minute (default: 60) |
| `CORS_ORIGINS` | Comma-separated allowed origins |

Configuration data (connections, semantic model, extra context) is stored in local files (`config.json` and `semantic_model.txt`).

### Running the Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Fill in your Azure OpenAI key and password
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker

```bash
cd backend
docker build -t pbichat-backend .
docker run -p 8000:8000 --env-file .env pbichat-backend
```

## Visual (Power BI Custom Visual)

### Tech Stack
- **TypeScript** — main visual logic
- **LESS** — styling (dark/light themes with toggle)
- **Chart.js** — inline chart rendering in chat responses
- **Power BI Visual Tools (pbiviz)** — build toolchain

### Visual Features

- **Inline data mode** — drag columns into "Columns" field well to chat about Power BI data instantly (no database setup needed). Visual computes summary stats from ALL rows + serializes CSV (400K char limit). Status bar shows "5 cols x 2,000 of 8,500 rows"
- **Database mode** — connect to Databricks or SQL Server for live SQL queries via agentic loop
- **Chat interface** with markdown rendering, code blocks, markdown tables, and inline Chart.js charts
- **Visual-first AI responses** — AI always presents data with charts, styled tables, and bold metric cards
- **Dark/light theme** toggle
- **Multi-connection support** — Databricks and SQL Server (per-report)
- **Settings panel** — password-protected
- **Semantic model upload** — multi-batch .tmdl file staging with connections (overwrite confirmation)
- **Schema-only mode** — TMDL files without DB connections enable AI to explain data structure, suggest DAX, write example SQL
- **No-data guard** — blocks chat in database mode without TMDL files or columns, shows setup instructions
- **In-app help modal** — step-by-step setup guide with tips (no external navigation)
- **Branded welcome screen** — embedded logo, setup instructions, suggestion grid
- **Warehouse auto-start** — backend sends Databricks START API call when warehouse is STOPPED
- **20-message chat history**
- **Microsoft Licensing API** — Free (3 queries/day, no charts) vs Starter/Business (unlimited queries, all 6 chart types)
- **SQL sanitization** — backend strips all SQL, code blocks, and `connection=` lines from LLM responses before returning to user

### Building the Visual

```bash
cd visual
npm install
npm run package
```

### Installing in Power BI

1. Build the `.pbiviz` package (see above)
2. In Power BI Desktop: Visualizations pane → `...` → Import a visual from a file
3. Select the `.pbiviz` file from `visual/dist/`
4. The visual appears in the visualizations pane — drag it onto a report page

## Scaling

- **Stateless backend** — configuration loaded from local files, no in-memory session state
- **Horizontal scaling** — ECS auto-scaling: min 2, max 10 tasks, CPU target 60%
- **Per-IP rate limiting** — sliding window with periodic cleanup
- **Schema cache** — LRU (max 1000 entries), 5-min TTL

## AWS Infrastructure

| Resource | Details |
|----------|---------|
| **ECS Cluster** | `pbichat` (Fargate) |
| **ECS Service** | `pbichat-backend` (auto-scaling: 2-10 tasks) |
| **Task Definition** | 1024 CPU, 2048 MB |
| **ALB** | `pbichat-alb` with HTTPS (ACM certificate) |
| **Domain** | Custom (configure in visual settings) |
| **Secrets** | AWS Secrets Manager (`pbichat/backend-env`) |
| **CI/CD** | CodeBuild → ECR → ECS |
| **Logs** | CloudWatch (`/ecs/pbichat-backend`) |
| **Auto-scaling** | CPU target 60%, scale-out cooldown 60s, scale-in cooldown 300s |

### Deployment

```bash
cd backend
./aws/deploy.sh
# Packages source → S3 → CodeBuild → ECR → ECS force deploy + auto-scaling setup
```

## AppSource Publication Progress

### Completed
- Phase 1: Project rebranding & cleanup
- Phase 2: Code quality & compliance (ESLint 0 errors)
- Phase 3: Security hardening (auth, rate limit, SQL safety, HTTPS)
- Phase 4: Feature polish (onboarding, errors, loading, Format Pane, a11y)
- Phase 5: Licensing (Microsoft Licensing API)
- Phase 6: Legal documents (Privacy Policy, EULA, ToS, DPA, Cookie Policy)
- Phase 8: AWS deployment (ECS Fargate, ALB, ACM, Route 53, CodeBuild, auto-scaling)
- Stateless backend with local config files
- TMDL slimming (culture files, metadata stripping — 95%+ size reduction)
- Visual-first AI presentation (charts, styled tables, metric cards)
- Markdown table rendering in chat responses
- In-app help modal with setup instructions
- Branded welcome screen with embedded logo
- Inline data mode (columns field well → CSV + stats → LLM analysis, no SQL)
- Warehouse auto-start (backend sends Databricks START API when STOPPED)
- SQL response sanitization (strip all SQL/code artifacts from LLM responses)
- Free tier: 3 queries/day, Pro: unlimited (Microsoft Licensing API)
- Schema-only mode (TMDL without DB connections — AI explains data model, suggests DAX/SQL)
- TMDL upload UX (mandatory model name, overwrite confirmation, `/auth/models/check-name` endpoint)
- Custom in-app confirm dialogs (Power BI iframe blocks browser `confirm()`)
- No-data guard (blocks chat without data source, shows setup instructions)
- Reading view support (`supportsEmptyDataView: true` — visual renders without data binding)

### Remaining
- Phase 7: Branding & marketing (screenshots, demo video, landing page)
- Phase 9: Partner Center account + AppSource submission
- Phase 10: Post-launch monitoring

## Pricing & Unit Economics

### LLM: DeepSeek V3.2 via OpenRouter
- Input: $0.26 / 1M tokens | Output: $0.38 / 1M tokens
- Cost per chat query: ~$0.003 (0.3 cents)

### Cost Per User Per Month

| User Type | Queries/day | LLM Cost/mo | Infra/mo (at 100 users) | Total Cost |
|-----------|-------------|-------------|-------------------------|------------|
| Light | 5 | $0.32 | ~$0.95 | ~$1.27 |
| Medium | 15 | $0.96 | ~$0.95 | ~$1.91 |
| Heavy | 50 | $3.20 | ~$0.95 | ~$4.15 |

### Fixed Infrastructure (~$93/mo)
- ECS Fargate (2 tasks, 1 vCPU, 2GB): ~$72/mo
- ALB: ~$20/mo
- Route 53: ~$1/mo

### Pricing Tiers

| Tier | Price | Reports Included | Extra Reports | Target |
|------|-------|------------------|---------------|--------|
| Starter | $9.99/user/mo | 3 reports | $3/mo each | Individuals, small teams |
| Business | $19.99/user/mo | 20 reports | $3/mo each | Teams, mid-market |

**Enterprise (25+ seats)**: Contact us for custom pricing (volume discounts, unlimited reports).

**Upgrade tipping point**: A Starter user with 7+ reports ($9.99 + 4 × $3 = $21.99) pays more than Business ($19.99). The math naturally drives upgrades.

### Break-Even (at $9.99/mo Starter, after Microsoft's 3% AppSource cut)
- Net revenue per user: $9.69
- Medium user cost: ~$1.91/mo
- Contribution margin: $7.78/user
- **Break-even: 13 Starter users** (or 6 Business users)

### Profitability at Scale (mixed tiers, avg $14/user)

| Users | Revenue (net) | Costs | Profit | Margin |
|-------|---------------|-------|--------|--------|
| 10 | $136/mo | $105 | $31 | 23% |
| 50 | $679/mo | $143 | $536 | 79% |
| 100 | $1,358/mo | $191 | $1,167 | 86% |
| 500 | $6,790/mo | $600 | $6,190 | 91% |
