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
| POST | `/chat` | Main endpoint — accepts user question, runs agentic SQL loop (license-enforced) |
| POST | `/config` | Update runtime config (password-protected) |
| GET | `/config` | Read current config (password-protected) |
| GET | `/connections` | List configured database connections (secrets redacted) |
| POST | `/connections` | Save database connections (license-enforced connection limit) |
| POST | `/upload-tmdl` | Upload .tmdl files from the visual's settings panel |
| POST | `/test-connection` | Test database connectivity |
| GET | `/license` | Check license tier and daily usage (public, no auth) |
| POST | `/auth/signup` | Create a new user account (Supabase Auth + user row + license key) |
| POST | `/auth/login` | Log in and receive JWT + license key + tier |
| POST | `/auth/refresh` | Refresh an expired JWT session |
| GET | `/auth/me` | Get current user profile + subscription status (JWT required) |
| POST | `/billing/create-checkout-session` | Create a Stripe Checkout session for Pro upgrade (JWT required) |
| POST | `/billing/webhook` | Stripe webhook receiver (signature-verified) |
| POST | `/billing/cancel-subscription` | Cancel Pro subscription at period end (JWT required) |
| POST | `/admin/licenses` | Create a new license key (admin only) |
| GET | `/admin/licenses` | List all license keys (admin only) |
| DELETE | `/admin/licenses/{key}` | Revoke a license key (admin only) |
| GET | `/admin/usage` | Usage stats per license key per day (admin only) |

### Environment Variables (`.env`)

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | API key for OpenRouter (routes to Claude) |
| `LLM_MODEL` | Model identifier (default: `anthropic/claude-sonnet-4`) |
| `EXTRA_CONTEXT` | Optional additional context injected into system prompt |
| `SETTINGS_PASSWORD` | Password for config/upload endpoints |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service role key |
| `SUPABASE_JWT_SECRET` | Supabase JWT secret (Settings > API) |
| `STRIPE_SECRET_KEY` | Stripe API secret key |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `STRIPE_PRICE_ID` | Stripe Price ID for the Pro plan ($15/mo) |
| `FREE_DAILY_QUERY_LIMIT` | Free tier daily query limit (default: `5`) |

Database connections (Databricks host/token, SQL Server credentials) are stored in `connections.json`, managed through the visual's settings panel. User accounts, license keys, subscriptions, and usage data are stored in Supabase (PostgreSQL).

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

## AppSource Publication Progress

Full plan details in [PLAN.md](PLAN.md).

### Phase 1: Project Rebranding & Cleanup — Complete
- Renamed all references from "Data Insights Assistant" to "PBIChat"
- Generated new unique GUID (`pbiChat_A849921B1A96`)
- Updated `pbiviz.json`, `package.json`, `capabilities.json`, backend `main.py`
- Narrowed WebAccess privilege from `["*"]` to `["https://*"]`
- Removed all development artifacts and real credentials from tracked files
- Created `.gitignore` covering secrets, build artifacts, and agent memory
- Initialized fresh git repository

### Phase 2: Code Quality & Compliance — Complete
- Installed ESLint with `eslint-plugin-powerbi-visuals` (flat config, ESLint v9)
- ESLint passes with 0 errors, 0 warnings
- `npm audit` passes with 0 vulnerabilities
- Updated `powerbi-visuals-api` to 5.11.0 and `powerbi-visuals-tools` to 7.0.2
- Hardened `formatMarkdown()` against XSS — HTML is escaped before markdown processing
- User input is always sanitized via `escapeHtml()` before DOM insertion
- Rendering Events API implemented (`renderingStarted` / `renderingFinished`)
- Certification audit run (informational) — 15 `fetch` calls flagged (expected, cannot certify due to external API calls)

### Phase 3: Security Hardening — Complete
- Removed hardcoded password — now set via `SETTINGS_PASSWORD` env var, validated server-side
- All protected endpoints require `X-Auth-Password` header (timing-safe comparison via `secrets.compare_digest`)
- HTTPS enforcement — visual warns when backend URL is HTTP (except localhost)
- Rate limiting — sliding-window per-IP limiter (configurable via `RATE_LIMIT_RPM`, default 60/min)
- SQL safety — destructive operations (DROP, ALTER, DELETE, INSERT, etc.) are blocked; only read-only queries allowed
- Input validation — chat messages validated for length (max 10K chars), password removed from request bodies
- Error message scrubbing — API keys and connection credentials are never exposed in error responses
- CORS configurable via `CORS_ORIGINS` env var with production restriction guidance

### Phase 4: Feature Polish for Commercial Release — Complete
- Onboarding/first-run setup banner with 4 numbered steps, auto-hides when backend is connected
- User-friendly error messages — maps network errors, 403, 429, 500, 502 to plain-language messages
- Loading states on async operations (Apply & Close button shows "Saving...")
- Help button in bottom bar (opens support URL via `host.launchUrl()`)
- Format Pane integration via `powerbi-visuals-utils-formattingmodel` — Backend URL configurable from Power BI's Format pane
- Responsive layout — bottom bar wraps on small tiles
- Keyboard accessibility — Escape closes overlays, `focus-visible` outlines for tab navigation
- High contrast mode — reads Power BI's `host.colorPalette` and overrides CSS custom properties with HC colors

### Phase 5: Freemium / Licensing Infrastructure — Complete
- **Tier design**: Free (5 queries/day, 1 connection, bar/line/pie) vs Pro ($15/mo, unlimited, all chart types)
- **User accounts**: Supabase Auth (email/password), JWT validation, user profile with linked license key
- **Database**: `users`, `subscriptions`, `payments`, `licenses`, `usage_log` tables with RLS policies
- **License keys**: Auto-generated `pbi-{uuid4}` on signup, linked to user; legacy standalone keys still work
- **Usage tracking**: Per user_id (logged in) or per IP (anonymous), counted daily
- **Stripe billing**: Checkout sessions for Pro upgrade, webhook handlers for payment events, cancel at period end
- **Auth UI**: Login/signup overlay with tabbed forms, "Continue without account" skip, session persistence via localStorage
- **User profile**: Account section in settings flyout (email, tier badge, Upgrade/Cancel/Logout)
- **Upgrade flow**: "Upgrade to Pro" CTA in limit banner and settings, opens Stripe Checkout, polls for tier change
- **Admin endpoints**: `POST/GET/DELETE /admin/licenses` for key management, `GET /admin/usage` for stats
- **Visual UI**: Tier badge in bottom bar, license key input in settings, admin key management panel
- **Format Pane**: License key configurable from Power BI's Format Pane alongside Backend URL
- **Backward compatible**: Old standalone license keys, IP-based free tier, and "Continue without account" all still work

### Phase 6–10: Pending
See [PLAN.md](PLAN.md) for remaining phases covering legal documents, branding, backend deployment, Partner Center submission, and post-launch.
