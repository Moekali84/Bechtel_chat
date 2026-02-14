# PBIChat — AppSource Publication Plan

## Overview
Prepare the "Data Insights Assistant" Power BI visual for publication on Microsoft AppSource as "PBIChat". This plan covers every step from rebranding to submission.

---

## Phase 1: Project Rebranding & Cleanup

- [ ] **1.1** Rename visual display name to "PBIChat" across all config files
- [ ] **1.2** Update `pbiviz.json` — new display name, description, author info, support URL, bump apiVersion to 5.9.1
- [ ] **1.3** Generate a new unique GUID for the visual (this is a new product, not an update to the old one)
- [ ] **1.4** Update `package.json` — name, description, author, license, homepage, repository fields
- [ ] **1.5** Update `capabilities.json` — replace WebAccess `["*"]` wildcard with specific allowed domains
- [ ] **1.6** Update class name and UI references from "Data Insights Assistant" to "PBIChat" in `visual.ts`
- [ ] **1.7** Update backend `main.py` — rename API title, update system prompt identity
- [ ] **1.8** Remove any development artifacts (connections.json with real credentials, .env with real keys, /dist, /node_modules, .tmp)
- [ ] **1.9** Create a proper `.gitignore` (must exclude: node_modules, .tmp, dist, .env, connections.json, *.pbiviz)
- [ ] **1.10** Initialize a fresh git repository for the new project

## Phase 2: Code Quality & Compliance

- [ ] **2.1** Install ESLint + eslint-plugin-powerbi-visuals as devDependencies
- [ ] **2.2** Add ESLint config file (`.eslintrc.json`) with powerbi-visuals plugin rules
- [ ] **2.3** Add eslint script to package.json: `"eslint": "npx eslint . --ext .js,.jsx,.ts,.tsx"`
- [ ] **2.4** Run ESLint and fix all errors (warnings OK, errors must be zero)
- [ ] **2.5** Run `npm audit` and fix any high/moderate vulnerabilities
- [ ] **2.6** Run `pbiviz package --certification-audit` to identify code issues (informational — we can't certify, but good practice)
- [ ] **2.7** Remove or replace any use of `innerHTML` where possible (required for certification, good practice for security)
- [ ] **2.8** Ensure all user inputs are sanitized before DOM insertion (XSS prevention)
- [ ] **2.9** Ensure the visual implements the Rendering Events API (`renderingStarted` / `renderingFinished`)
- [ ] **2.10** Update `powerbi-visuals-tools` to the latest version
- [ ] **2.11** Update `powerbi-visuals-api` to the latest version

## Phase 3: Security Hardening

- [ ] **3.1** Move hardcoded password ("Safari99") to a configurable setting — users must set their own password on first use
- [ ] **3.2** Implement HTTPS enforcement — warn users if backend URL is not HTTPS (except localhost for dev)
- [ ] **3.3** Add API key/token authentication between visual and backend (not just a static password)
- [ ] **3.4** Investigate implementing the Power BI Authentication API (AADAuthentication) for SSO
- [ ] **3.5** Add rate limiting to the backend API
- [ ] **3.6** Sanitize all SQL queries on the backend to prevent SQL injection
- [ ] **3.7** Add input validation on all backend endpoints
- [ ] **3.8** Ensure no sensitive data (API keys, tokens) is logged or exposed in error messages
- [ ] **3.9** Add CORS configuration guidance — restrict origins in production (not `*`)

## Phase 4: Feature Polish for Commercial Release

- [ ] **4.1** Add a proper onboarding/first-run experience (guided setup wizard instead of raw settings panel)
- [ ] **4.2** Add error messages that are user-friendly (not raw stack traces or API errors)
- [ ] **4.3** Add loading states for all async operations
- [ ] **4.4** Add a "Help" button or link in the UI
- [ ] **4.5** Add visual property pane integration (Power BI Format pane) for basic settings like backend URL
- [ ] **4.6** Add telemetry/usage tracking (opt-in) for understanding user behavior
- [ ] **4.7** Test and fix responsive layout at all sizes (small tile, large tile, full page, phone layout)
- [ ] **4.8** Add keyboard accessibility (Tab navigation, Enter to submit, Escape to close)
- [ ] **4.9** Add high contrast mode support (Power BI requirement for accessibility)
- [ ] **4.10** Test in all required browsers: Chrome, Edge, Firefox (current versions on Windows)
- [ ] **4.11** Test in Power BI Desktop (current version)
- [ ] **4.12** Test pinned to a Power BI Dashboard

## Phase 5: Freemium / Licensing Infrastructure

- [ ] **5.1** Design the free vs. paid feature split:
  - Free: 5 queries/day, single connection, basic charts
  - Pro: unlimited queries, multiple connections, all chart types, export
- [ ] **5.2** Implement query counting on the backend (per-user or per-session)
- [ ] **5.3** Implement license validation (check against a licensing server or use Microsoft Licensing API)
- [ ] **5.4** Add license status indicator in the UI (Free / Pro badge)
- [ ] **5.5** Add upgrade prompt when free limits are reached (pop-up with purchase link)
- [ ] **5.6** Decide on pricing: $10–25/user/month recommended for Pro tier
- [ ] **5.7** Set up payment processing (Stripe, or use Microsoft's AppSource transact system with 3% fee)
- [ ] **5.8** Create a pricing page on your website

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
