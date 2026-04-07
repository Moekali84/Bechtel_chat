# PBIChat — Development Plan

## Overview
Internal AI-powered chat assistant for Power BI with live SQL access to Databricks and SQL Server.

---

## Phase 1: Project Rebranding & Cleanup

- [x] **1.1** Rename visual display name to "PBIChat" across all config files
- [x] **1.2** Update `pbiviz.json` — new display name, description, author info, support URL, apiVersion 5.11.0
- [x] **1.3** Generate a new unique GUID for the visual (`pbiChat_A849921B1A96`)
- [x] **1.4** Update `package.json` — name, description, author, license, homepage, repository fields
- [x] **1.5** Update `capabilities.json` — replace WebAccess `["*"]` wildcard with `["https://*"]`
- [x] **1.6** Update class name and UI references from "Data Insights Assistant" to "PBIChat" in `visual.ts`
- [x] **1.7** Update backend `main.py` — rename API title, update system prompt identity
- [x] **1.8** Remove any development artifacts (connections.json with real credentials, .env with real keys, /dist, /node_modules, .tmp)
- [x] **1.9** Create a proper `.gitignore` (must exclude: node_modules, .tmp, dist, .env, connections.json, *.pbiviz)
- [x] **1.10** Initialize a fresh git repository for the new project

## Phase 2: Code Quality & Compliance

- [x] **2.1** Install ESLint + eslint-plugin-powerbi-visuals as devDependencies
- [x] **2.2** Add ESLint flat config (`eslint.config.mjs`) with powerbi-visuals recommended rules
- [x] **2.3** Add eslint script to package.json
- [x] **2.4** Run ESLint and fix all errors — 0 errors, 0 warnings
- [x] **2.5** Run `npm audit` and fix all vulnerabilities — 0 vulnerabilities
- [x] **2.6** Run `pbiviz package --certification-audit` — builds successfully, 15 `fetch` calls flagged (expected)
- [x] **2.7** innerHTML usage audited — kept with warnings (required for chat rendering), XSS mitigated
- [x] **2.8** All user inputs sanitized via `escapeHtml()`, LLM output HTML-escaped before markdown processing
- [x] **2.9** Rendering Events API implemented (`renderingStarted` / `renderingFinished` in `update()`)
- [x] **2.10** Update `powerbi-visuals-tools` to 7.0.2
- [x] **2.11** Update `powerbi-visuals-api` to 5.11.0

## Phase 3: Security Hardening

- [x] **3.1** Removed hardcoded "Safari99" — password now set via `SETTINGS_PASSWORD` env var, validated against backend
- [x] **3.2** HTTPS enforcement — visual shows warning when backend URL is HTTP and not localhost/127.0.0.1
- [x] **3.3** Settings protected by `SETTINGS_PASSWORD` env var with timing-safe comparison (`secrets.compare_digest`)
- [x] **3.4** Investigated AAD Authentication API — requires Power BI premium capacity and Azure AD app registration; deferred to post-launch (Phase 10)
- [x] **3.5** Rate limiting — sliding-window per-IP limiter (`RATE_LIMIT_RPM` env var, default 60/min) on `/chat` and `/verify-password`
- [x] **3.6** SQL injection prevention — `_BLOCKED_SQL` regex blocks destructive operations (DROP, ALTER, DELETE, UPDATE, INSERT, CREATE, TRUNCATE, GRANT, REVOKE, EXEC, MERGE); only read-only queries allowed
- [x] **3.7** Input validation — `ChatRequest.message` validates non-empty and max 10,000 chars; password fields removed from request bodies
- [x] **3.8** Sensitive data scrubbing — LLM API errors scrub API keys and passwords; SQL Server errors scrub connection credentials
- [x] **3.9** CORS configurable via `CORS_ORIGINS` env var (default `*`); `.env.example` documents production restriction guidance

## Phase 4: Feature Polish

- [x] **4.1** Add a proper onboarding/first-run experience — setup banner with 4 numbered steps, auto-hides when connected
- [x] **4.2** Add error messages that are user-friendly — `showError()` maps common technical errors to friendly messages
- [x] **4.3** Add loading states for all async operations — "Saving..." state on Apply & Close button
- [x] **4.4** Add a "Help" button in the bottom bar — opens https://pbichat.com/support via `host.launchUrl()`
- [x] **4.5** Add visual property pane integration — Format Pane via `powerbi-visuals-utils-formattingmodel` (Backend URL setting)
- [ ] **4.6** ~~Add telemetry/usage tracking~~ — deferred to post-launch
- [x] **4.7** Responsive layout — `flex-wrap` on bottom bar for small tiles
- [x] **4.8** Keyboard accessibility — Escape closes overlays, global `focus-visible` outlines
- [x] **4.9** High contrast mode — `applyHighContrast()` reads `host.colorPalette`, sets CSS custom properties, adds `dia-hc` borders
- [ ] **4.10** Test in Chrome, Edge, Firefox — manual testing required
- [ ] **4.11** Test in Power BI Desktop — manual testing required
- [ ] **4.12** Test pinned to a Power BI Dashboard — manual testing required

## Phase 5: Backend Deployment & Hosting

- [x] **5.1** Choose a hosting platform for the backend — AWS ECS Fargate selected
- [x] **5.2** Set up production deployment with HTTPS — ALB with ACM certificate, Route 53 DNS
- [x] **5.3** Set up CI/CD pipeline for backend updates — CodeBuild → ECR → ECS via `deploy.sh`
- [x] **5.4** Set up monitoring and alerting — CloudWatch logs (`/ecs/pbichat-backend`)
- [x] **5.5** Set up logging (structured logs, no sensitive data)
- [ ] **5.6** Document the backend deployment process
- [x] **5.7** Create a Docker image — Dockerfile in `backend/`
- [x] **5.8** Set up auto-scaling — ECS auto-scaling: min 2, max 10 tasks, CPU target 60%

## Phase 6: Ongoing Maintenance

- [ ] **6.1** Track Power BI API changelog for breaking changes
- [ ] **6.2** Track dependency updates and security patches
- [ ] **6.3** Plan a regular update cadence

---

## Key Reference Links

| Resource | URL |
|---|---|
| Publishing Guidelines | https://learn.microsoft.com/en-us/power-bi/developer/visuals/guidelines-powerbi-visuals |
| Submission Testing | https://learn.microsoft.com/en-us/power-bi/developer/visuals/submission-testing |
| Capabilities & Properties | https://learn.microsoft.com/en-us/power-bi/developer/visuals/capabilities |
| Authentication API (SSO) | https://learn.microsoft.com/en-us/power-bi/developer/visuals/authentication-api |
