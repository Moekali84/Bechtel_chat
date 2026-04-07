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
└── README.md
```

## Backend

### Tech Stack
- **Python 3.12** with FastAPI + uvicorn
- **httpx** for async HTTP calls to Azure OpenAI and Databricks
- **pymssql** for SQL Server connectivity
- **pydantic** for request/response validation

### Configuration

| Data | Storage |
|------|---------|
| Database connections | `config.json` |
| Semantic model (TMDL) | `semantic_model.txt` |
| Extra context | `config.json` |
| API keys | `.env` environment variables |

### Environment Variables (`.env`)

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `LLM_MODEL` | Model deployment name on Azure OpenAI |
| `SETTINGS_PASSWORD` | Password for accessing the settings panel |
| `RATE_LIMIT_RPM` | Per-IP requests per minute (default: 60) |
| `CORS_ORIGINS` | Comma-separated allowed origins |

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

### TMDL Slimming
Large semantic models are automatically optimized before inclusion in the LLM prompt:
- Culture/locale files (e.g. `en-US.tmdl`) are stripped entirely
- Non-essential metadata (lineageTag, annotation, summarizeBy) removed
- Consecutive blank lines collapsed
- Hard truncation at 400K characters
- Typical reduction: 95%+ (e.g. 1.25M → 55K characters)

### Running the Backend

#### Quick Start Scripts (Windows)
```powershell
# PowerShell (recommended)
cd backend
.\start-backend.ps1

# Or CMD
start-backend.bat
```

These scripts automatically check Python installation, create a virtual environment, install dependencies, start the uvicorn server, and perform a health check.

#### Manual Setup
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\Activate.ps1
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

### Features

- **Inline data mode** — drag columns into "Columns" field well to chat about Power BI data instantly (no database setup needed). Visual computes summary stats from ALL rows + serializes CSV (400K char limit)
- **Database mode** — connect to Databricks or SQL Server for live SQL queries via agentic loop
- **Schema-only mode** — TMDL files without DB connections enable AI to explain data structure, suggest DAX, write example SQL
- **Chat interface** with markdown rendering, code blocks, markdown tables, and inline Chart.js charts
- **Visual-first AI responses** — AI presents data with charts, styled tables, and bold metric cards
- **Dark/light theme** toggle
- **Multi-connection support** — Databricks and SQL Server (per-report)
- **Settings panel** — password-protected
- **Semantic model upload** — multi-batch .tmdl file staging with overwrite confirmation
- **No-data guard** — blocks chat without data source, shows setup instructions
- **In-app help modal** — step-by-step setup guide
- **Branded welcome screen** — embedded logo, setup instructions, suggestion grid
- **Warehouse auto-start** — sends Databricks START API call when warehouse is STOPPED
- **20-message chat history**
- **SQL sanitization** — backend strips all SQL, code blocks, and `connection=` lines from LLM responses

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

## Deployment (AWS)

| Resource | Details |
|----------|---------|
| **ECS Cluster** | `pbichat` (Fargate) |
| **ECS Service** | `pbichat-backend` (auto-scaling: 2-10 tasks) |
| **Task Definition** | 1024 CPU, 2048 MB |
| **ALB** | `pbichat-alb` with HTTPS (ACM certificate) |
| **Secrets** | AWS Secrets Manager (`pbichat/backend-env`) |
| **CI/CD** | CodeBuild → ECR → ECS |
| **Logs** | CloudWatch (`/ecs/pbichat-backend`) |
| **Auto-scaling** | CPU target 60%, scale-out cooldown 60s, scale-in cooldown 300s |

```bash
cd backend
./aws/deploy.sh
# Packages source → S3 → CodeBuild → ECR → ECS force deploy + auto-scaling setup
```
