# PBIChat — AppSource Publication Plan

## Overview
Prepare the "Data Insights Assistant" Power BI visual for publication on Microsoft AppSource as "PBIChat". This plan covers every step from rebranding to submission.

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
- [x] **3.3** Auth via `X-Auth-Password` header on all protected endpoints using `Depends(require_auth)` with timing-safe comparison (`secrets.compare_digest`)
- [x] **3.4** Investigated AAD Authentication API — requires Power BI premium capacity and Azure AD app registration; deferred to post-launch (Phase 10)
- [x] **3.5** Rate limiting — sliding-window per-IP limiter (`RATE_LIMIT_RPM` env var, default 60/min) on `/chat` and `/verify-password`
- [x] **3.6** SQL injection prevention — `_BLOCKED_SQL` regex blocks destructive operations (DROP, ALTER, DELETE, UPDATE, INSERT, CREATE, TRUNCATE, GRANT, REVOKE, EXEC, MERGE); only read-only queries allowed
- [x] **3.7** Input validation — `ChatRequest.message` validates non-empty and max 10,000 chars; password fields removed from request bodies
- [x] **3.8** Sensitive data scrubbing — LLM API errors scrub API keys and passwords; SQL Server errors scrub connection credentials
- [x] **3.9** CORS configurable via `CORS_ORIGINS` env var (default `*`); `.env.example` documents production restriction guidance

## Phase 4: Feature Polish for Commercial Release

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

## Phase 5: Freemium / Licensing Infrastructure

- [x] **5.1** Designed free vs. paid feature split — Free: 5 queries/day, 1 connection, bar/line/pie charts; Pro: unlimited queries, unlimited connections, all 6 chart types
- [x] **5.2** Query counting via Supabase `usage_log` table — per license key (Pro) or per IP (Free), counted daily
- [x] **5.3** License validation via Supabase `licenses` table — `pbi-{uuid4}` keys with tier, expiry, active/revoked status; `resolve_license()` + `resolve_license_dep` FastAPI dependency
- [x] **5.4** License status indicator — tier badge in bottom bar ("FREE (3/5)" yellow / "PRO" green), `GET /license` endpoint for status check
- [x] **5.5** Upgrade prompt — styled banner with lock icon when daily limit reached, directs user to enter Pro key in Settings
- [x] **5.6** Decided on pricing — $15/user/month for Pro tier
- [x] **5.7** Set up payment processing — Stripe Checkout with monthly recurring subscriptions
- [ ] **5.8** Create a pricing page on your website (business decision)
- [x] **5.9** User account system — Supabase Auth (email/password signup + login), JWT validation, user profile endpoint
- [x] **5.10** User-linked license keys — signup auto-generates `pbi-{uuid}` key linked to user row, replaces anonymous model
- [x] **5.11** Stripe billing endpoints — `POST /billing/create-checkout-session`, `POST /billing/webhook` (5 event handlers), `POST /billing/cancel-subscription`
- [x] **5.12** Database schema — `users`, `subscriptions`, `payments` tables with RLS policies; `usage_log` extended with `user_id` FK
- [x] **5.13** Visual auth overlay — tabbed Login/Sign Up form with "Continue without account" skip
- [x] **5.14** Visual user profile — Account section in settings with email, tier badge, Upgrade/Cancel/Logout buttons
- [x] **5.15** Stripe upgrade flow — opens Checkout via `host.launchUrl()`, polls `/auth/me` for tier change
- [x] **5.16** Session persistence — JWT tokens stored in localStorage, auto-refresh on 401
- [x] **5.17** Backward compatibility — old standalone license keys still work via fallback in `resolve_license`

## Phase 6: Legal & Compliance Documents

- [ ] **6.1** Write and host a **Privacy Policy** (HTTPS URL) covering:
  - What data the visual collects
  - How data is transmitted to the backend
  - Where data is stored/processed
  - Data retention policies
  - Third-party data sharing (OpenRouter/LLM provider)
  - User rights (access, deletion, portability)
- [ ] **6.2** Write an **End-User License Agreement (EULA)** — or use Microsoft's Standard Contract
- [ ] **6.3** Write **Terms of Service** for the backend API
- [ ] **6.4** Write a **Data Processing Agreement (DPA)** for EU/GDPR customers
- [ ] **6.5** Create a **Cookie Policy** if your website uses cookies
- [ ] **6.6** Register a business entity if not already done (required for Partner Center)

## Phase 7: Branding & Marketing Assets

- [ ] **7.1** Design a logo (300x300 px PNG for AppSource, 20x20 px PNG for the visual pane icon)
- [ ] **7.2** Create 1-5 screenshots (exactly 1366x768 px PNG, max 1024 KB each) with descriptive text bubbles
- [ ] **7.3** Record a demo video (1-3 minutes, HTTPS URL — upload to YouTube or similar)
- [ ] **7.4** Write the AppSource listing description (short summary + detailed description)
- [ ] **7.5** Create a product website/landing page with:
  - Features overview
  - Screenshots/demo
  - Pricing
  - Documentation
  - Support contact
  - Privacy policy & terms
- [ ] **7.6** Create a support page or help desk (GitHub Issues, Freshdesk, Zendesk, or email)
- [ ] **7.7** Write setup/installation documentation for end users
- [ ] **7.8** Create a sample `.pbix` file that demonstrates the visual's value (must work offline)

## Phase 8: Backend Deployment & Hosting

- [ ] **8.1** Choose a hosting platform for the backend (Azure App Service, AWS, GCP, Databricks Apps, etc.)
- [ ] **8.2** Set up production deployment with HTTPS (SSL certificate)
- [ ] **8.3** Set up CI/CD pipeline for backend updates
- [ ] **8.4** Set up monitoring and alerting (uptime, error rates, response times)
- [ ] **8.5** Set up logging (structured logs, no sensitive data)
- [ ] **8.6** Document the backend deployment process for self-hosted customers
- [ ] **8.7** Create a Docker image for easy self-hosting
- [ ] **8.8** Set up a managed/hosted option for customers who don't want to self-host

## Phase 9: Partner Center & Submission

- [ ] **9.1** Create a [Microsoft Partner Center](https://partner.microsoft.com) developer account
- [ ] **9.2** Complete the publisher profile (company info, tax info, payout info)
- [ ] **9.3** Create a new Power BI visual offer in Partner Center
- [ ] **9.4** Upload the `.pbiviz` package on the Technical Configuration page
- [ ] **9.5** Upload the sample `.pbix` file
- [ ] **9.6** Fill in all marketing details (name, summary, description, logos, screenshots, video)
- [ ] **9.7** Configure pricing model (Free, Freemium, or Licensed)
- [ ] **9.8** Provide legal documents (EULA, privacy policy URL)
- [ ] **9.9** Submit for validation
- [ ] **9.10** Review the visual in the test environment after Microsoft approval
- [ ] **9.11** Click "Go Live" to publish

## Phase 10: Post-Launch

- [ ] **10.1** Monitor AppSource reviews and respond to feedback
- [ ] **10.2** Set up a customer feedback channel
- [ ] **10.3** Plan a regular update cadence (monthly or quarterly)
- [ ] **10.4** Track Power BI API changelog for breaking changes
- [ ] **10.5** Track dependency updates and security patches
- [ ] **10.6** Create a roadmap for future features
- [ ] **10.7** Set up analytics to track adoption, usage, and conversion rates

---

## Key Reference Links

| Resource | URL |
|---|---|
| Publish to Partner Center | https://learn.microsoft.com/en-us/power-bi/developer/visuals/office-store |
| Publishing Guidelines | https://learn.microsoft.com/en-us/power-bi/developer/visuals/guidelines-powerbi-visuals |
| Certification Requirements | https://learn.microsoft.com/en-us/power-bi/developer/visuals/power-bi-custom-visuals-certified |
| Submission Testing | https://learn.microsoft.com/en-us/power-bi/developer/visuals/submission-testing |
| Capabilities & Properties | https://learn.microsoft.com/en-us/power-bi/developer/visuals/capabilities |
| Authentication API (SSO) | https://learn.microsoft.com/en-us/power-bi/developer/visuals/authentication-api |
| License Models | https://learn.microsoft.com/en-us/power-bi/developer/visuals/custom-visual-licenses |
| Partner Center Setup | https://learn.microsoft.com/en-us/partner-center/marketplace-offers/marketplace-power-bi-visual |
| Microsoft Publisher Agreement | https://learn.microsoft.com/en-us/legal/marketplace/msft-publisher-agreement |
| Standard Contract (EULA option) | https://learn.microsoft.com/en-us/partner-center/marketplace-offers/standard-contract |
| Marketplace Fees (3%) | https://learn.microsoft.com/en-us/partner-center/marketplace-offers/marketplace-commercial-transaction-capabilities-and-considerations |

---

## Notes

- **Cannot be Microsoft-certified** due to external API calls — this is expected and OK for AppSource
- **Review timeline**: up to 4 weeks for new visuals, 10-14 days to appear in Power BI after approval
- **Microsoft takes 3%** on transacted sales (extremely favorable vs Apple/Google 30%)
- **Biggest competitor**: Microsoft Copilot for Power BI (bundled with Pro at $10/user/mo)
- **Key differentiators**: Direct SQL to Databricks/SQL Server, self-hosted backend, bring-your-own-LLM, data sovereignty
