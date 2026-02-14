# PBIChat

AI-powered chat assistant for Power BI with live SQL access to Databricks and SQL Server. Ask questions in natural language, get instant data-driven answers with interactive charts.

## Architecture

```
Power BI Desktop / Service
  └── PBIChat Visual (TypeScript/LESS)
        └── HTTP requests
              └── PBIChat Backend (Python/FastAPI)
                    ├── LLM (Claude via OpenRouter)
                    └── Database connections (Databricks / SQL Server)
```

**Agentic loop:** User question → LLM → SQL → Database → Results → LLM → Factual response (up to 5 iterations per question)

## Project Structure

```
PBIChat/
├── backend/
│   ├── main.py              # FastAPI app — all endpoints, agentic loop, system prompt
│   ├── .env                  # Runtime config (API keys — not committed)
│   ├── .env.example          # Template for .env
│   ├── connections.json      # Database connections (not committed)
│   ├── semantic_model.txt    # Uploaded TMDL content (persisted on disk)
│   ├── requirements.txt      # Python dependencies
│   └── Dockerfile            # Container build for deployment
├── visual/
│   ├── src/visual.ts         # Main Power BI visual class (TypeScript)
│   ├── style/visual.less     # All visual styling (dark/light themes)
│   ├── pbiviz.json           # Visual metadata and config
│   ├── capabilities.json     # Power BI capabilities declaration
│   ├── tsconfig.json         # TypeScript config
│   └── package.json          # Node dependencies
├── PLAN.md                   # AppSource publication plan
└── README.md
```

## Backend

### Tech Stack
- **Python 3.12** with FastAPI + uvicorn
- **httpx** for async HTTP calls to OpenRouter and Databricks
- **pymssql** for SQL Server connectivity
- **pydantic** for request/response validation
- **python-dotenv** for environment config

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check — returns connection and LLM config status |
| GET | `/warehouse-status` | Returns current Databricks warehouse state |
| POST | `/chat` | Main endpoint — accepts user question, runs agentic SQL loop |
| POST | `/config` | Update runtime config (password-protected) |
| GET | `/config` | Read current config (password-protected) |
| GET | `/connections` | List configured database connections (secrets redacted) |
| POST | `/connections` | Save database connections |
| POST | `/upload-tmdl` | Upload .tmdl files from the visual's settings panel |
| POST | `/test-connection` | Test database connectivity |

### Environment Variables (`.env`)

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | API key for OpenRouter (routes to Claude) |
| `LLM_MODEL` | Model identifier (default: `anthropic/claude-sonnet-4`) |
| `EXTRA_CONTEXT` | Optional additional context injected into system prompt |
| `SETTINGS_PASSWORD` | Password for config/upload endpoints |

Database connections (Databricks host/token, SQL Server credentials) are stored in `connections.json`, managed through the visual's settings panel.

### Running the Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Fill in your API key and password
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

- **Chat interface** with markdown rendering, code blocks, and inline Chart.js charts
- **Dark/light theme** toggle
- **Multi-connection support** — Databricks and SQL Server
- **Settings panel** with password protection
- **Semantic model upload** — multi-batch .tmdl file staging
- **20-message chat history**

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

## First-Time Setup

1. **Start the backend** (see Running the Backend above)
2. **Import the visual** into Power BI Desktop
3. **Open Settings** (gear icon in the visual)
4. Enter the settings password
5. Set the Backend API URL
6. Add database connections (Databricks and/or SQL Server)
7. Upload TMDL files from your semantic model
8. Start asking questions in the chat
