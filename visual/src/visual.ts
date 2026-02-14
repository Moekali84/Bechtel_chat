/* eslint-disable powerbi-visuals/no-inner-outer-html */
import powerbi from "powerbi-visuals-api";
import VisualConstructorOptions = powerbi.extensibility.visual.VisualConstructorOptions;
import VisualUpdateOptions = powerbi.extensibility.visual.VisualUpdateOptions;
import IVisual = powerbi.extensibility.visual.IVisual;
import IVisualHost = powerbi.extensibility.visual.IVisualHost;
import { formattingSettings, FormattingSettingsService } from "powerbi-visuals-utils-formattingmodel";

import { Chart, registerables } from "chart.js";
import "./../style/visual.less";

Chart.register(...registerables);

interface HistoryMsg {
    role: "user" | "assistant";
    content: string;
}

class ConnectionCard extends formattingSettings.SimpleCard {
    name = "connection";
    displayName = "Connection";
    backendUrl = new formattingSettings.TextInput({
        name: "backendUrl",
        displayName: "Backend API URL",
        description: "URL of your deployed PBIChat backend API",
        placeholder: "https://your-app.azurewebsites.net",
        value: "",
    });
    licenseKey = new formattingSettings.TextInput({
        name: "licenseKey",
        displayName: "License Key",
        description: "Pro license key (optional — leave blank for free tier)",
        placeholder: "pbi-...",
        value: "",
    });
    slices: formattingSettings.Slice[] = [this.backendUrl, this.licenseKey];
}

class PBIChatFormattingSettings extends formattingSettings.Model {
    connection = new ConnectionCard();
    cards: formattingSettings.SimpleCard[] = [this.connection];
}

export class PBIChat implements IVisual {
    private host: IVisualHost;
    private container: HTMLElement;
    private _backendUrl: string = "http://localhost:8000";
    private history: HistoryMsg[] = [];
    private authPassword: string = "";  // password for backend auth
    private fmtSettings = new PBIChatFormattingSettings();
    private fmtService: FormattingSettingsService;

    private get backendUrl(): string { return this._backendUrl; }
    private set backendUrl(val: string) {
        let url = (val || "").trim().replace(/\/+$/, "");
        // eslint-disable-next-line powerbi-visuals/no-http-string
        if (url && !/^https?:\/\//i.test(url)) url = "http://" + url;
        this._backendUrl = url;
    }
    private busy: boolean = false;
    private isDarkTheme: boolean = true;
    private extraContext: string = "";
    private tmdlLoaded: boolean = false;
    private pendingTmdlFiles: { name: string; content: string }[] = [];
    private connections: Array<{
        id: string; name: string; type: string;
        host?: string; http_path?: string; token?: string; catalog_schema?: string;
        server?: string; database?: string; username?: string; password?: string;
    }> = [];

    // Warehouse status tracking
    private warehouseState: string = "";
    private statusPollTimer: number | null = null;
    private lastBackendUrl: string = "";

    // License state
    private licenseKey: string = "";
    private licenseTier: string = "free";
    private dailyUsed: number = 0;
    private dailyLimit: number | null = 5;
    private allowedCharts: string[] = ["bar", "line", "pie"];

    // Auth state
    private accessToken: string = "";
    private refreshToken: string = "";
    private userId: string = "";
    private userEmail: string = "";
    private userDisplayName: string = "";
    private isLoggedIn: boolean = false;
    private upgradePollTimer: number | null = null;

    // DOM references
    private chatEl: HTMLElement;
    private msgsEl: HTMLElement;
    private inputEl: HTMLTextAreaElement;
    private sendBtn: HTMLButtonElement;
    private welcomeEl: HTMLElement;

    /** Fetch wrapper that adds auth, license, and JWT headers. Auto-refreshes on 401. */
    private async authFetch(url: string, init: RequestInit = {}): Promise<Response> {
        const makeHeaders = () => {
            const headers = new Headers(init.headers || {});
            if (this.authPassword) headers.set("X-Auth-Password", this.authPassword);
            if (this.licenseKey) headers.set("X-License-Key", this.licenseKey);
            if (this.accessToken) headers.set("Authorization", `Bearer ${this.accessToken}`);
            return headers;
        };
        const resp = await fetch(url, { ...init, headers: makeHeaders() });
        // Auto-refresh JWT on 401 and retry once
        if (resp.status === 401 && this.refreshToken) {
            const refreshed = await this.refreshSession();
            if (refreshed) {
                return fetch(url, { ...init, headers: makeHeaders() });
            }
        }
        return resp;
    }

    constructor(options: VisualConstructorOptions) {
        this.host = options.host;
        this.fmtService = new FormattingSettingsService();
        this.container = options.element;
        this.container.innerHTML = "";
        this.buildUI();
        this.restoreSession();
    }

    // ══════════════════════════════════════
    // BUILD THE CHAT UI
    // ══════════════════════════════════════
    private buildUI(): void {
        this.container.innerHTML = `
        <div class="dia-root">
            <div class="dia-chat" id="dia-chat">
                <div class="dia-msgs" id="dia-msgs">
                    <div class="dia-welcome" id="dia-welcome">
                        <h1>PBIChat</h1>
                        <p class="dia-welcome-sub">AI-powered data assistant with live SQL access</p>
                        <div class="dia-setup-banner" id="dia-setup-banner">
                            <div class="dia-setup-title">Get Started</div>
                            <div class="dia-setup-steps">
                                <div class="dia-setup-step"><span class="dia-step-num">1</span> Click <strong>Settings</strong> below and enter the admin password</div>
                                <div class="dia-setup-step"><span class="dia-step-num">2</span> Set your <strong>Backend API URL</strong> and <strong>API key</strong></div>
                                <div class="dia-setup-step"><span class="dia-step-num">3</span> Add a <strong>database connection</strong> (Databricks or SQL Server)</div>
                                <div class="dia-setup-step"><span class="dia-step-num">4</span> Upload your <strong>.tmdl files</strong> and click Apply</div>
                            </div>
                        </div>
                        <div class="dia-sugs">
                            <button class="dia-sug" data-q="What tables are in my database?">What tables are in my database?</button>
                            <button class="dia-sug" data-q="Show me a summary of the data">Show me a summary of the data</button>
                            <button class="dia-sug" data-q="Write a DAX measure for this metric">Write a DAX measure for this metric</button>
                            <button class="dia-sug" data-q="What trends or anomalies exist?">What trends or anomalies exist?</button>
                        </div>
                    </div>
                </div>
            </div>
            <div class="dia-input-wrap">
                <div class="dia-input-container">
                    <div class="dia-input-box">
                        <textarea id="dia-input" placeholder="Ask anything about your data..." rows="1"></textarea>
                        <button class="dia-send" id="dia-send">
                            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9L22 2Z"/>
                            </svg>
                        </button>
                    </div>
                    <div class="dia-bottom-bar">
                        <button class="dia-bottom-btn" id="dia-settings-btn"><span class="dia-dot" id="dia-conn-dot"></span> Settings</button>
                        <button class="dia-bottom-btn" id="dia-clear-btn">Clear chat</button>
                        <span class="dia-tier-badge dia-tier-free" id="dia-tier-badge">FREE</span>
                        <button class="dia-bottom-btn" id="dia-theme-btn">
                            <svg class="dia-theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
                            </svg>
                            Light
                        </button>
                        <button class="dia-bottom-btn" id="dia-help-btn">
                            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                            </svg>
                            Help
                        </button>
                        <div class="dia-status" id="dia-status"></div>
                    </div>
                </div>
            </div>

            <!-- Password gate -->
            <div class="dia-pw-overlay" id="dia-pw-overlay">
                <div class="dia-pw-box">
                    <h3>Settings Locked</h3>
                    <p>Enter the admin password to access settings.</p>
                    <input type="password" id="dia-pw-input" placeholder="Password"/>
                    <div class="dia-pw-error" id="dia-pw-error"></div>
                    <div class="dia-pw-btns">
                        <button class="dia-pw-cancel" id="dia-pw-cancel">Cancel</button>
                        <button class="dia-pw-ok" id="dia-pw-ok">Unlock</button>
                    </div>
                </div>
            </div>

            <!-- Settings flyout -->
            <div class="dia-settings-overlay" id="dia-settings-overlay"></div>
            <div class="dia-settings" id="dia-settings">
                <div class="dia-settings-header">
                    <h3>Settings</h3>
                    <button class="dia-settings-close" id="dia-settings-close">&#x2715;</button>
                </div>
                <div class="dia-settings-body">
                    <div id="dia-account-section"></div>
                    <div class="dia-settings-section">Connection</div>
                    <div class="dia-field">
                        <label>Backend API URL</label>
                        <input type="text" id="dia-s-url" value="http://localhost:8000" placeholder="http://localhost:8000"/>
                        <div class="dia-hint">URL of your PBIChat backend server.</div>
                        <div id="dia-https-warn" class="dia-https-warn" style="display:none">Warning: Using HTTP instead of HTTPS. Data will be sent unencrypted. Use HTTPS in production.</div>
                    </div>

                    <div class="dia-settings-section">OpenRouter API</div>
                    <div class="dia-field">
                        <label>API Key</label>
                        <input type="password" id="dia-s-apikey" placeholder="sk-or-v1-..."/>
                    </div>
                    <div class="dia-field">
                        <label>Model</label>
                        <input type="text" id="dia-s-model" placeholder="anthropic/claude-sonnet-4"/>
                    </div>

                    <div class="dia-settings-section">Data Connections</div>
                    <div id="dia-conn-list"></div>
                    <button class="dia-test-btn" id="dia-add-conn-btn">+ Add Connection</button>

                    <div class="dia-settings-section">Semantic Model (.tmdl Files)</div>
                    <div class="dia-tmdl-actions">
                        <button class="dia-test-btn" id="dia-add-tmdl-btn">Add .tmdl Files</button>
                        <button class="dia-test-btn dia-btn-muted" id="dia-tmdl-clear-btn" style="display:none">Clear All</button>
                        <input type="file" id="dia-tmdl-file-input" multiple accept=".tmdl" style="display:none"/>
                    </div>
                    <div class="dia-hint">Select .tmdl files from one or more folders. You can click "Add" multiple times to combine files from different locations.</div>
                    <div class="dia-tmdl-file-list" id="dia-tmdl-file-list" style="display:none"></div>
                    <button class="dia-test-btn dia-btn-primary" id="dia-tmdl-upload-btn" style="display:none">Upload to Backend</button>
                    <div class="dia-test-result" id="dia-tmdl-result"></div>

                    <div class="dia-settings-section">Additional Context</div>
                    <div class="dia-field">
                        <label>Business rules, notes, or extra schema</label>
                        <textarea id="dia-s-extra" rows="3" placeholder="e.g. OSHA rates use 200,000 multiplier."></textarea>
                    </div>

                    <div class="dia-settings-section">License</div>
                    <div class="dia-field">
                        <label>License Key (optional)</label>
                        <input type="text" id="dia-s-license" placeholder="pbi-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"/>
                        <div class="dia-hint">Enter a Pro license key for unlimited queries and all chart types. Leave blank for free tier (5 queries/day).</div>
                    </div>

                    <div class="dia-settings-section">License Management (Admin)</div>
                    <div class="dia-field">
                        <label>Create New License Key</label>
                        <div style="display:flex;gap:6px;">
                            <input type="text" id="dia-lic-label" placeholder="Label (e.g. Acme Corp)" style="flex:1"/>
                            <button class="dia-test-btn dia-btn-primary" id="dia-lic-create-btn">Generate Key</button>
                        </div>
                        <div class="dia-test-result" id="dia-lic-result"></div>
                    </div>
                    <div id="dia-lic-list"></div>

                    <div class="dia-tmdl-warning" id="dia-tmdl-warning">Upload TMDL files before applying settings.</div>
                    <button class="dia-apply-btn" id="dia-apply-btn" disabled>Apply &amp; Close</button>
                </div>
            </div>
        </div>`;

        // Get references
        this.chatEl = this.container.querySelector("#dia-chat")!;
        this.msgsEl = this.container.querySelector("#dia-msgs")!;
        this.inputEl = this.container.querySelector("#dia-input") as HTMLTextAreaElement;
        this.sendBtn = this.container.querySelector("#dia-send") as HTMLButtonElement;
        this.welcomeEl = this.container.querySelector("#dia-welcome")!;

        // Chat event listeners
        this.sendBtn.addEventListener("click", () => this.send());
        this.inputEl.addEventListener("keydown", (e: KeyboardEvent) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.send(); }
        });
        this.inputEl.addEventListener("input", () => {
            this.inputEl.style.height = "auto";
            this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 150) + "px";
        });

        // Suggestion buttons
        this.container.querySelectorAll(".dia-sug").forEach((btn) => {
            btn.addEventListener("click", (e) => {
                const q = (e.target as HTMLElement).getAttribute("data-q") || "";
                this.inputEl.value = q;
                this.send();
            });
        });

        // Settings button
        this.container.querySelector("#dia-settings-btn")!.addEventListener("click", () => this.openSettings());

        // Clear chat button
        this.container.querySelector("#dia-clear-btn")!.addEventListener("click", () => this.clearChat());

        // Theme toggle
        this.container.querySelector("#dia-theme-btn")!.addEventListener("click", () => this.toggleTheme());
        // Help button
        this.container.querySelector("#dia-help-btn")!.addEventListener("click", () => {
            this.host.launchUrl("https://pbichat.com/support");
        });

        // Password gate
        this.container.querySelector("#dia-pw-ok")!.addEventListener("click", () => this.checkPassword());
        this.container.querySelector("#dia-pw-cancel")!.addEventListener("click", () => this.closePwGate());
        (this.container.querySelector("#dia-pw-input") as HTMLInputElement).addEventListener("keydown", (e: KeyboardEvent) => {
            if (e.key === "Enter") this.checkPassword();
        });

        // Settings panel
        this.container.querySelector("#dia-settings-close")!.addEventListener("click", () => this.closeSettings());
        this.container.querySelector("#dia-settings-overlay")!.addEventListener("click", () => this.closeSettings());
        (this.container.querySelector("#dia-s-url") as HTMLInputElement).addEventListener("input", () => this.checkHttpsWarning());
        this.container.querySelector("#dia-add-conn-btn")!.addEventListener("click", () => this.addConnection());
        this.container.querySelector("#dia-add-tmdl-btn")!.addEventListener("click", () => {
            (this.container.querySelector("#dia-tmdl-file-input") as HTMLInputElement).click();
        });
        (this.container.querySelector("#dia-tmdl-file-input") as HTMLInputElement).addEventListener("change", (e) => this.addTmdlFiles(e));
        this.container.querySelector("#dia-tmdl-clear-btn")!.addEventListener("click", () => this.clearTmdlFiles());
        this.container.querySelector("#dia-tmdl-upload-btn")!.addEventListener("click", () => this.uploadTmdlFiles());
        this.container.querySelector("#dia-apply-btn")!.addEventListener("click", () => this.applySettings());
        this.container.querySelector("#dia-lic-create-btn")!.addEventListener("click", () => this.createLicense());

        // Global keyboard handler — Escape closes overlays
        this.container.addEventListener("keydown", (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                const pwOverlay = this.container.querySelector("#dia-pw-overlay");
                if (pwOverlay?.classList.contains("show")) {
                    this.closePwGate();
                    return;
                }
                const settingsPanel = this.container.querySelector("#dia-settings");
                if (settingsPanel?.classList.contains("show")) {
                    this.closeSettings();
                }
            }
        });
    }

    // ══════════════════════════════════════
    // AUTH SESSION MANAGEMENT
    // ══════════════════════════════════════
    private setAuthState(data: {
        access_token?: string; refresh_token?: string;
        user_id?: string; email?: string; display_name?: string;
        tier?: string; license_key?: string;
    }): void {
        if (data.access_token) this.accessToken = data.access_token;
        if (data.refresh_token) this.refreshToken = data.refresh_token;
        if (data.user_id) this.userId = data.user_id;
        if (data.email) this.userEmail = data.email;
        if (data.display_name !== undefined) this.userDisplayName = data.display_name;
        if (data.tier) this.licenseTier = data.tier;
        if (data.license_key) this.licenseKey = data.license_key;
        this.isLoggedIn = !!this.accessToken;
        this.persistSession();
        this.renderTierBadge();
    }

    private persistSession(): void {
        try {
            const session = {
                accessToken: this.accessToken,
                refreshToken: this.refreshToken,
                userId: this.userId,
                userEmail: this.userEmail,
                userDisplayName: this.userDisplayName,
                licenseTier: this.licenseTier,
                licenseKey: this.licenseKey,
            };
            localStorage.setItem("pbichat_session", JSON.stringify(session));
        } catch (_) { /* localStorage not available */ }
    }

    private restoreSession(): void {
        try {
            const raw = localStorage.getItem("pbichat_session");
            if (!raw) return;
            const s = JSON.parse(raw);
            this.accessToken = s.accessToken || "";
            this.refreshToken = s.refreshToken || "";
            this.userId = s.userId || "";
            this.userEmail = s.userEmail || "";
            this.userDisplayName = s.userDisplayName || "";
            this.licenseTier = s.licenseTier || "free";
            this.licenseKey = s.licenseKey || "";
            this.isLoggedIn = !!this.accessToken;
            this.renderTierBadge();
        } catch (_) { /* localStorage not available or corrupt */ }
    }

    private logout(): void {
        this.accessToken = "";
        this.refreshToken = "";
        this.userId = "";
        this.userEmail = "";
        this.userDisplayName = "";
        this.isLoggedIn = false;
        this.licenseTier = "free";
        this.licenseKey = "";
        try { localStorage.removeItem("pbichat_session"); } catch (_) { /* ok */ }
        this.renderTierBadge();
        this.renderAccountSection();
    }

    private async refreshSession(): Promise<boolean> {
        if (!this.refreshToken || !this.backendUrl) return false;
        try {
            const resp = await fetch(`${this.backendUrl}/auth/refresh`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ refresh_token: this.refreshToken }),
            });
            if (!resp.ok) return false;
            const data = await resp.json();
            this.accessToken = data.access_token;
            this.refreshToken = data.refresh_token;
            this.isLoggedIn = true;
            this.persistSession();
            return true;
        } catch (_) { return false; }
    }

    // ══════════════════════════════════════
    // AUTH OVERLAY (Login / Signup)
    // ══════════════════════════════════════
    private showAuthOverlay(): void {
        let overlay = this.container.querySelector("#dia-auth-overlay") as HTMLElement;
        if (!overlay) {
            overlay = document.createElement("div");
            overlay.className = "dia-auth-overlay";
            overlay.id = "dia-auth-overlay";
            overlay.innerHTML = `
                <div class="dia-auth-box">
                    <h3>Welcome to PBIChat</h3>
                    <div class="dia-auth-tabs">
                        <button class="dia-auth-tab active" data-tab="login">Log In</button>
                        <button class="dia-auth-tab" data-tab="signup">Sign Up</button>
                    </div>
                    <div class="dia-auth-form" id="dia-auth-login">
                        <div class="dia-field">
                            <label>Email</label>
                            <input type="email" id="dia-login-email" placeholder="you@example.com"/>
                        </div>
                        <div class="dia-field">
                            <label>Password</label>
                            <input type="password" id="dia-login-pw" placeholder="Password"/>
                        </div>
                        <div class="dia-auth-error" id="dia-login-error"></div>
                        <button class="dia-apply-btn dia-auth-submit" id="dia-login-btn">Log In</button>
                    </div>
                    <div class="dia-auth-form" id="dia-auth-signup" style="display:none">
                        <div class="dia-field">
                            <label>Email</label>
                            <input type="email" id="dia-signup-email" placeholder="you@example.com"/>
                        </div>
                        <div class="dia-field">
                            <label>Display Name</label>
                            <input type="text" id="dia-signup-name" placeholder="Your name (optional)"/>
                        </div>
                        <div class="dia-field">
                            <label>Password</label>
                            <input type="password" id="dia-signup-pw" placeholder="Min 6 characters"/>
                        </div>
                        <div class="dia-auth-error" id="dia-signup-error"></div>
                        <button class="dia-apply-btn dia-auth-submit" id="dia-signup-btn">Create Account</button>
                    </div>
                    <button class="dia-auth-skip" id="dia-auth-skip">Continue without account</button>
                </div>`;
            this.container.querySelector(".dia-root")!.appendChild(overlay);

            // Tab switching
            overlay.querySelectorAll(".dia-auth-tab").forEach(tab => {
                tab.addEventListener("click", () => {
                    overlay.querySelectorAll(".dia-auth-tab").forEach(t => t.classList.remove("active"));
                    tab.classList.add("active");
                    const target = (tab as HTMLElement).dataset.tab;
                    (overlay.querySelector("#dia-auth-login") as HTMLElement).style.display = target === "login" ? "" : "none";
                    (overlay.querySelector("#dia-auth-signup") as HTMLElement).style.display = target === "signup" ? "" : "none";
                });
            });

            // Login submit
            overlay.querySelector("#dia-login-btn")!.addEventListener("click", () => this.handleLogin());
            (overlay.querySelector("#dia-login-pw") as HTMLInputElement).addEventListener("keydown", (e: KeyboardEvent) => {
                if (e.key === "Enter") this.handleLogin();
            });

            // Signup submit
            overlay.querySelector("#dia-signup-btn")!.addEventListener("click", () => this.handleSignup());
            (overlay.querySelector("#dia-signup-pw") as HTMLInputElement).addEventListener("keydown", (e: KeyboardEvent) => {
                if (e.key === "Enter") this.handleSignup();
            });

            // Skip button
            overlay.querySelector("#dia-auth-skip")!.addEventListener("click", () => {
                this.hideAuthOverlay();
            });
        }
        overlay.style.display = "flex";
        setTimeout(() => (overlay.querySelector("#dia-login-email") as HTMLInputElement)?.focus(), 100);
    }

    private hideAuthOverlay(): void {
        const overlay = this.container.querySelector("#dia-auth-overlay") as HTMLElement;
        if (overlay) overlay.style.display = "none";
    }

    private async handleLogin(): Promise<void> {
        const email = (this.container.querySelector("#dia-login-email") as HTMLInputElement).value.trim();
        const pw = (this.container.querySelector("#dia-login-pw") as HTMLInputElement).value;
        const errEl = this.container.querySelector("#dia-login-error") as HTMLElement;
        if (!email || !pw) { errEl.textContent = "Please fill in all fields."; return; }
        if (!this.backendUrl) { errEl.textContent = "Backend URL not configured."; return; }

        errEl.textContent = "Logging in...";
        try {
            const resp = await fetch(`${this.backendUrl}/auth/login`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password: pw }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                errEl.textContent = err.detail || "Login failed.";
                return;
            }
            const data = await resp.json();
            this.setAuthState({
                access_token: data.access_token,
                refresh_token: data.refresh_token,
                user_id: data.user_id,
                email: data.email,
                display_name: data.display_name,
                tier: data.tier,
                license_key: data.license_key,
            });
            this.hideAuthOverlay();
            this.fetchLicenseStatus();
        } catch (e: any) {
            errEl.textContent = "Cannot reach server.";
        }
    }

    private async handleSignup(): Promise<void> {
        const email = (this.container.querySelector("#dia-signup-email") as HTMLInputElement).value.trim();
        const name = (this.container.querySelector("#dia-signup-name") as HTMLInputElement).value.trim();
        const pw = (this.container.querySelector("#dia-signup-pw") as HTMLInputElement).value;
        const errEl = this.container.querySelector("#dia-signup-error") as HTMLElement;
        if (!email || !pw) { errEl.textContent = "Email and password are required."; return; }
        if (pw.length < 6) { errEl.textContent = "Password must be at least 6 characters."; return; }
        if (!this.backendUrl) { errEl.textContent = "Backend URL not configured."; return; }

        errEl.textContent = "Creating account...";
        try {
            const resp = await fetch(`${this.backendUrl}/auth/signup`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password: pw, display_name: name }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                errEl.textContent = err.detail || "Signup failed.";
                return;
            }
            const data = await resp.json();
            if (data.access_token) {
                this.setAuthState({
                    access_token: data.access_token,
                    refresh_token: data.refresh_token,
                    user_id: data.user_id,
                    email: data.email,
                    tier: data.tier,
                    license_key: data.license_key,
                });
                this.hideAuthOverlay();
            } else {
                // Email confirmation required
                errEl.textContent = "";
                const successEl = document.createElement("div");
                successEl.className = "dia-test-result ok";
                successEl.style.display = "block";
                successEl.textContent = "Account created! Check your email to confirm, then log in.";
                errEl.parentElement?.insertBefore(successEl, errEl);
                // Switch to login tab
                setTimeout(() => {
                    (this.container.querySelector('.dia-auth-tab[data-tab="login"]') as HTMLElement)?.click();
                }, 2000);
            }
        } catch (e: any) {
            errEl.textContent = "Cannot reach server.";
        }
    }

    // ══════════════════════════════════════
    // STRIPE UPGRADE FLOW
    // ══════════════════════════════════════
    private async initiateUpgrade(): Promise<void> {
        if (!this.backendUrl || !this.accessToken) {
            this.showAuthOverlay();
            return;
        }
        try {
            const resp = await this.authFetch(`${this.backendUrl}/billing/create-checkout-session`, {
                method: "POST",
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                if (resp.status === 401) {
                    this.showAuthOverlay();
                    return;
                }
                this.showError(err.detail || "Could not start upgrade.");
                return;
            }
            const data = await resp.json();
            if (data.checkout_url) {
                this.host.launchUrl(data.checkout_url);
                this.startUpgradeCheck();
            }
        } catch (e: any) {
            this.showError("Could not reach server for upgrade.");
        }
    }

    private startUpgradeCheck(): void {
        this.stopUpgradeCheck();
        let polls = 0;
        this.upgradePollTimer = window.setInterval(async () => {
            polls++;
            if (polls > 60) { this.stopUpgradeCheck(); return; } // 5 min timeout
            try {
                const resp = await this.authFetch(`${this.backendUrl}/auth/me`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (data.tier === "pro") {
                        this.stopUpgradeCheck();
                        this.setAuthState({ tier: "pro" });
                        this.renderAccountSection();
                        this.fetchLicenseStatus();
                    }
                }
            } catch (_) { /* keep polling */ }
        }, 5000) as unknown as number;
    }

    private stopUpgradeCheck(): void {
        if (this.upgradePollTimer !== null) {
            clearInterval(this.upgradePollTimer);
            this.upgradePollTimer = null;
        }
    }

    // ══════════════════════════════════════
    // SETTINGS / PASSWORD
    // ══════════════════════════════════════
    private openSettings(): void {
        this.container.querySelector("#dia-pw-overlay")!.classList.add("show");
        (this.container.querySelector("#dia-pw-input") as HTMLInputElement).value = "";
        (this.container.querySelector("#dia-pw-error") as HTMLElement).textContent = "";
        setTimeout(() => (this.container.querySelector("#dia-pw-input") as HTMLInputElement).focus(), 100);
    }

    private async checkPassword(): Promise<void> {
        const pw = (this.container.querySelector("#dia-pw-input") as HTMLInputElement).value;
        const errEl = this.container.querySelector("#dia-pw-error") as HTMLElement;
        if (!pw) {
            errEl.textContent = "Please enter a password.";
            return;
        }
        // Validate against the backend
        errEl.textContent = "Verifying...";
        try {
            const resp = await fetch(`${this.backendUrl}/verify-password`, {
                method: "POST",
                headers: { "X-Auth-Password": pw },
            });
            if (resp.ok) {
                this.authPassword = pw;
                this.closePwGate();
                this.showSettingsPanel();
            } else {
                errEl.textContent = "Incorrect password.";
                (this.container.querySelector("#dia-pw-input") as HTMLInputElement).value = "";
                (this.container.querySelector("#dia-pw-input") as HTMLInputElement).focus();
            }
        } catch (_) {
            errEl.textContent = "Cannot reach backend to verify password.";
        }
    }

    private closePwGate(): void {
        this.container.querySelector("#dia-pw-overlay")!.classList.remove("show");
    }

    private showSettingsPanel(): void {
        this.container.querySelector("#dia-settings")!.classList.add("show");
        this.container.querySelector("#dia-settings-overlay")!.classList.add("show");

        // Populate backend URL field
        (this.container.querySelector("#dia-s-url") as HTMLInputElement).value = this.backendUrl;
        (this.container.querySelector("#dia-s-license") as HTMLInputElement).value = this.licenseKey;
        this.checkHttpsWarning();
        this.updateApplyButton();
        this.renderAccountSection();

        // Fetch current config from backend to populate all fields
        if (this.backendUrl) {
            this.loadConfigFromBackend();
            this.loadLicenses();
        }
    }

    private renderAccountSection(): void {
        const el = this.container.querySelector("#dia-account-section") as HTMLElement;
        if (!el) return;

        if (!this.isLoggedIn) {
            el.innerHTML = `
                <div class="dia-settings-section">Account</div>
                <div class="dia-account-prompt">
                    <p>Log in or create an account to track your usage and upgrade to Pro.</p>
                    <button class="dia-test-btn dia-btn-primary" id="dia-account-login-btn">Log In / Sign Up</button>
                </div>`;
            el.querySelector("#dia-account-login-btn")?.addEventListener("click", () => {
                this.closeSettings();
                this.showAuthOverlay();
            });
            return;
        }

        const tierBadge = this.licenseTier === "pro"
            ? '<span class="dia-tier-badge dia-tier-pro">PRO</span>'
            : '<span class="dia-tier-badge dia-tier-free">FREE</span>';

        let subInfo = "";
        let actionBtn = "";

        if (this.licenseTier === "pro") {
            subInfo = '<div class="dia-user-sub-info">Active Pro subscription</div>';
            actionBtn = '<button class="dia-test-btn" id="dia-cancel-sub-btn">Cancel Subscription</button>';
        } else {
            subInfo = '<div class="dia-user-sub-info">Free tier &mdash; upgrade for unlimited queries &amp; all chart types</div>';
            actionBtn = '<button class="dia-test-btn dia-btn-primary dia-upgrade-cta" id="dia-upgrade-btn">Upgrade to Pro &mdash; $15/mo</button>';
        }

        el.innerHTML = `
            <div class="dia-settings-section">Account</div>
            <div class="dia-account-card">
                <div class="dia-user-email">${this.escapeHtml(this.userEmail)} ${tierBadge}</div>
                ${subInfo}
                <div style="display:flex;gap:8px;margin-top:8px;">
                    ${actionBtn}
                    <button class="dia-test-btn" id="dia-logout-btn">Log Out</button>
                </div>
            </div>`;

        el.querySelector("#dia-upgrade-btn")?.addEventListener("click", () => {
            this.closeSettings();
            this.initiateUpgrade();
        });
        el.querySelector("#dia-cancel-sub-btn")?.addEventListener("click", () => this.cancelSubscription());
        el.querySelector("#dia-logout-btn")?.addEventListener("click", () => {
            this.logout();
            this.closeSettings();
        });
    }

    private async cancelSubscription(): Promise<void> {
        const btn = this.container.querySelector("#dia-cancel-sub-btn") as HTMLButtonElement;
        if (btn) { btn.textContent = "Canceling..."; btn.disabled = true; }
        try {
            const resp = await this.authFetch(`${this.backendUrl}/billing/cancel-subscription`, { method: "POST" });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                if (btn) { btn.textContent = err.detail || "Failed"; btn.disabled = false; }
                return;
            }
            if (btn) { btn.textContent = "Canceled at period end"; }
        } catch (_) {
            if (btn) { btn.textContent = "Error"; btn.disabled = false; }
        }
    }

    private checkHttpsWarning(): void {
        const urlVal = (this.container.querySelector("#dia-s-url") as HTMLInputElement).value.trim().toLowerCase();
        const warnEl = this.container.querySelector("#dia-https-warn") as HTMLElement;
        if (!warnEl) return;
        // Show warning if URL is HTTP and not localhost
        // eslint-disable-next-line powerbi-visuals/no-http-string
        const isInsecure = urlVal.startsWith("http://") && !urlVal.startsWith("http://localhost") && !urlVal.startsWith("http://127.0.0.1");
        warnEl.style.display = isInsecure ? "block" : "none";
    }

    private async loadConfigFromBackend(): Promise<void> {
        try {
            const resp = await this.authFetch(`${this.backendUrl}/config`);
            if (!resp.ok) return;
            const cfg = await resp.json();

            const setVal = (id: string, val: string) => {
                const el = this.container.querySelector(id) as HTMLInputElement | HTMLTextAreaElement;
                if (el) el.value = val || "";
            };

            setVal("#dia-s-apikey", cfg.openrouter_api_key);
            setVal("#dia-s-model", cfg.llm_model);
            setVal("#dia-s-extra", cfg.extra_context);

            // Show TMDL load status if model is loaded
            if (cfg.semantic_model_loaded) {
                this.tmdlLoaded = true;
                const tmdlResult = this.container.querySelector("#dia-tmdl-result") as HTMLElement;
                if (tmdlResult) {
                    tmdlResult.className = "dia-test-result ok";
                    tmdlResult.style.display = "block";
                    tmdlResult.textContent = `TMDL loaded (${(cfg.semantic_model_chars / 1024).toFixed(1)} KB)`;
                }
            }

            this.extraContext = cfg.extra_context || "";

            // Load connections
            try {
                const connResp = await this.authFetch(`${this.backendUrl}/connections`);
                if (connResp.ok) {
                    const connData = await connResp.json();
                    this.connections = connData.connections || [];
                }
            } catch (_) { /* connections endpoint not available */ }

            this.renderConnectionList();
            this.updateApplyButton();
        } catch (e) {
            // Backend unreachable — fields stay empty
        }
    }

    private closeSettings(): void {
        this.container.querySelector("#dia-settings")!.classList.remove("show");
        this.container.querySelector("#dia-settings-overlay")!.classList.remove("show");
    }

    // ── Theme toggle ──

    private toggleTheme(): void {
        this.isDarkTheme = !this.isDarkTheme;
        const root = this.container.querySelector(".dia-root");
        if (root) {
            root.classList.toggle("dia-light", !this.isDarkTheme);
        }

        const btn = this.container.querySelector("#dia-theme-btn");
        if (btn) {
            if (this.isDarkTheme) {
                btn.innerHTML = `
                    <svg class="dia-theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
                    </svg>
                    Light`;
            } else {
                btn.innerHTML = `
                    <svg class="dia-theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
                    </svg>
                    Dark`;
            }
        }
    }

    private get chartTheme() {
        return this.isDarkTheme ? {
            text: "#ececec",
            textSec: "#a8a7a2",
            textMuted: "#7a7972",
            tooltipBg: "rgba(30,30,28,0.95)",
            tooltipBody: "#c8c7c2",
            tooltipBorder: "rgba(255,255,255,0.08)",
            grid: "rgba(255,255,255,0.04)",
            pieBorder: "rgba(30,30,28,0.8)",
            pieHoverBorder: "rgba(255,255,255,0.15)",
            pointBorder: "#1e1e1c",
        } : {
            text: "#1c1b19",
            textSec: "#5d5c56",
            textMuted: "#8c8b85",
            tooltipBg: "rgba(255,255,255,0.96)",
            tooltipBody: "#5c5b55",
            tooltipBorder: "rgba(0,0,0,0.1)",
            grid: "rgba(0,0,0,0.06)",
            pieBorder: "rgba(255,255,255,0.9)",
            pieHoverBorder: "rgba(0,0,0,0.08)",
            pointBorder: "#ffffff",
        };
    }

    // ── Connection CRUD ──

    private slugify(name: string): string {
        return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "conn";
    }

    private addConnection(): void {
        const idx = this.connections.length;
        this.connections.push({
            id: `conn-${idx + 1}`,
            name: `Connection ${idx + 1}`,
            type: "databricks",
        });
        this.renderConnectionList();
    }

    private removeConnection(idx: number): void {
        this.connections.splice(idx, 1);
        this.renderConnectionList();
    }

    private renderConnectionList(): void {
        const listEl = this.container.querySelector("#dia-conn-list") as HTMLElement;
        if (!listEl) return;
        listEl.innerHTML = "";

        this.connections.forEach((conn, idx) => {
            const card = document.createElement("div");
            card.className = "dia-conn-card";

            const dbFields = conn.type === "databricks" ? `
                <div class="dia-field">
                    <label>Workspace URL</label>
                    <input type="text" class="dia-conn-f" data-idx="${idx}" data-field="host" value="${this.escapeHtml(conn.host || "")}" placeholder="https://adb-xxxx.azuredatabricks.net"/>
                </div>
                <div class="dia-field">
                    <label>SQL Warehouse HTTP Path</label>
                    <input type="text" class="dia-conn-f" data-idx="${idx}" data-field="http_path" value="${this.escapeHtml(conn.http_path || "")}" placeholder="/sql/1.0/warehouses/abc123"/>
                </div>
                <div class="dia-field">
                    <label>Access Token</label>
                    <input type="password" class="dia-conn-f" data-idx="${idx}" data-field="token" value="${this.escapeHtml(conn.token || "")}" placeholder="dapi..."/>
                </div>
                <div class="dia-field">
                    <label>Default Catalog.Schema</label>
                    <input type="text" class="dia-conn-f" data-idx="${idx}" data-field="catalog_schema" value="${this.escapeHtml(conn.catalog_schema || "")}" placeholder="my_catalog.my_schema"/>
                </div>
            ` : `
                <div class="dia-field">
                    <label>Server</label>
                    <input type="text" class="dia-conn-f" data-idx="${idx}" data-field="server" value="${this.escapeHtml(conn.server || "")}" placeholder="myserver.database.windows.net"/>
                </div>
                <div class="dia-field">
                    <label>Database</label>
                    <input type="text" class="dia-conn-f" data-idx="${idx}" data-field="database" value="${this.escapeHtml(conn.database || "")}" placeholder="ReportingDB"/>
                </div>
                <div class="dia-field">
                    <label>Username</label>
                    <input type="text" class="dia-conn-f" data-idx="${idx}" data-field="username" value="${this.escapeHtml(conn.username || "")}" placeholder="sa"/>
                </div>
                <div class="dia-field">
                    <label>Password</label>
                    <input type="password" class="dia-conn-f" data-idx="${idx}" data-field="password" value="${this.escapeHtml(conn.password || "")}" placeholder="..."/>
                </div>
            `;

            card.innerHTML = `
                <div class="dia-conn-card-header">
                    <input type="text" class="dia-conn-name" data-idx="${idx}" value="${this.escapeHtml(conn.name || "")}" placeholder="Connection name"/>
                    <select class="dia-conn-type" data-idx="${idx}">
                        <option value="databricks" ${conn.type === "databricks" ? "selected" : ""}>Databricks</option>
                        <option value="sqlserver" ${conn.type === "sqlserver" ? "selected" : ""}>SQL Server</option>
                    </select>
                    <button class="dia-conn-remove" data-idx="${idx}">&times;</button>
                </div>
                <div class="dia-conn-fields">${dbFields}</div>
                <div class="dia-conn-card-footer">
                    <button class="dia-test-btn dia-conn-test" data-idx="${idx}">Test</button>
                    <div class="dia-test-result dia-conn-result" data-idx="${idx}"></div>
                </div>
            `;
            listEl.appendChild(card);
        });

        // Bind events for all cards
        listEl.querySelectorAll<HTMLSelectElement>(".dia-conn-type").forEach((sel) => {
            sel.addEventListener("change", () => {
                const i = parseInt(sel.dataset.idx || "0");
                this.readConnectionsFromUI();
                this.connections[i].type = sel.value;
                // Clear type-specific fields when switching
                if (sel.value === "databricks") {
                    delete this.connections[i].server;
                    delete this.connections[i].database;
                    delete this.connections[i].username;
                    delete this.connections[i].password;
                } else {
                    delete this.connections[i].host;
                    delete this.connections[i].http_path;
                    delete this.connections[i].token;
                    delete this.connections[i].catalog_schema;
                }
                this.renderConnectionList();
            });
        });

        listEl.querySelectorAll<HTMLButtonElement>(".dia-conn-remove").forEach((btn) => {
            btn.addEventListener("click", () => {
                this.readConnectionsFromUI();
                this.removeConnection(parseInt(btn.dataset.idx || "0"));
            });
        });

        listEl.querySelectorAll<HTMLButtonElement>(".dia-conn-test").forEach((btn) => {
            btn.addEventListener("click", () => {
                this.readConnectionsFromUI();
                this.testSingleConnection(parseInt(btn.dataset.idx || "0"));
            });
        });
    }

    private readConnectionsFromUI(): void {
        const listEl = this.container.querySelector("#dia-conn-list");
        if (!listEl) return;

        // Read name inputs
        listEl.querySelectorAll<HTMLInputElement>(".dia-conn-name").forEach((el) => {
            const i = parseInt(el.dataset.idx || "0");
            if (this.connections[i]) {
                this.connections[i].name = el.value.trim();
                this.connections[i].id = this.slugify(el.value.trim()) || this.connections[i].id;
            }
        });

        // Read field inputs
        listEl.querySelectorAll<HTMLInputElement>(".dia-conn-f").forEach((el) => {
            const i = parseInt(el.dataset.idx || "0");
            const field = el.dataset.field || "";
            if (this.connections[i] && field) {
                (this.connections[i] as any)[field] = el.value.trim();
            }
        });
    }

    private async testSingleConnection(idx: number): Promise<void> {
        this.readConnectionsFromUI();
        const conn = this.connections[idx];
        if (!conn) return;

        const resultEl = this.container.querySelector(`.dia-conn-result[data-idx="${idx}"]`) as HTMLElement;
        if (!resultEl) return;
        resultEl.className = "dia-test-result dia-conn-result";
        resultEl.style.display = "block";
        resultEl.textContent = "Testing...";

        const url = this.getSettingsUrl();
        if (!url) {
            resultEl.className = "dia-test-result dia-conn-result fail";
            resultEl.textContent = "Set Backend API URL first.";
            return;
        }

        try {
            // Save connections first so the backend knows about this connection
            await this.authFetch(`${url}/connections`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ connections: this.connections }),
            });

            const resp = await this.authFetch(`${url}/test-connection/${conn.id}`, { method: "POST" });
            if (resp.ok) {
                resultEl.className = "dia-test-result dia-conn-result ok";
                resultEl.textContent = "Connected!";
            } else {
                const err = await resp.json().catch(() => ({ detail: `Status ${resp.status}` }));
                resultEl.className = "dia-test-result dia-conn-result fail";
                resultEl.textContent = err.detail || "Connection failed.";
            }
        } catch (e: any) {
            resultEl.className = "dia-test-result dia-conn-result fail";
            resultEl.textContent = e.message || "Cannot reach backend.";
        }
    }

    private async addTmdlFiles(e: Event): Promise<void> {
        const input = e.target as HTMLInputElement;
        const files = input.files;
        if (!files || files.length === 0) return;

        for (let i = 0; i < files.length; i++) {
            const text = await files[i].text();
            const name = files[i].name;
            // Dedup by name — newer file replaces older
            const idx = this.pendingTmdlFiles.findIndex(f => f.name === name);
            if (idx >= 0) {
                this.pendingTmdlFiles[idx].content = text;
            } else {
                this.pendingTmdlFiles.push({ name, content: text });
            }
        }

        input.value = "";
        this.renderTmdlFileList();
    }

    private clearTmdlFiles(): void {
        this.pendingTmdlFiles = [];
        this.renderTmdlFileList();
        const resultEl = this.container.querySelector("#dia-tmdl-result") as HTMLElement;
        if (resultEl) { resultEl.style.display = "none"; }
    }

    private renderTmdlFileList(): void {
        const listEl = this.container.querySelector("#dia-tmdl-file-list") as HTMLElement;
        const clearBtn = this.container.querySelector("#dia-tmdl-clear-btn") as HTMLElement;
        const uploadBtn = this.container.querySelector("#dia-tmdl-upload-btn") as HTMLElement;
        const count = this.pendingTmdlFiles.length;

        if (count === 0) {
            listEl.style.display = "none";
            clearBtn.style.display = "none";
            uploadBtn.style.display = "none";
            return;
        }

        clearBtn.style.display = "";
        uploadBtn.style.display = "";
        listEl.style.display = "block";

        const names = this.pendingTmdlFiles.map(f => f.name).sort();
        listEl.innerHTML =
            `<div class="dia-tmdl-count">${count} file${count !== 1 ? "s" : ""} staged</div>` +
            names.map(n => `<div class="dia-tmdl-file-item">${n}</div>`).join("");
    }

    private async uploadTmdlFiles(): Promise<void> {
        if (this.pendingTmdlFiles.length === 0) return;

        const resultEl = this.container.querySelector("#dia-tmdl-result") as HTMLElement;
        resultEl.className = "dia-test-result";
        resultEl.style.display = "block";
        resultEl.textContent = `Uploading ${this.pendingTmdlFiles.length} file(s)...`;

        const url = this.getSettingsUrl();
        if (!url) {
            resultEl.className = "dia-test-result fail";
            resultEl.textContent = "Set the Backend API URL first.";
            return;
        }

        try {
            const resp = await this.authFetch(`${url}/upload-tmdl`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: this.pendingTmdlFiles }),
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `Error ${resp.status}`);
            }

            const data = await resp.json();
            resultEl.className = "dia-test-result ok";
            const skipped = data.files_skipped ? ` (${data.files_skipped} auto-generated skipped)` : "";
            resultEl.textContent = `Uploaded ${data.files_loaded} .tmdl file(s) (${(data.total_chars / 1024).toFixed(1)} KB)${skipped}`;
            this.pendingTmdlFiles = [];
            this.renderTmdlFileList();
            this.tmdlLoaded = true;
            this.updateApplyButton();
        } catch (err: any) {
            resultEl.className = "dia-test-result fail";
            resultEl.textContent = err.message || "Failed to upload TMDL files.";
            // Keep files staged so user can retry
        }
    }

    private updateApplyButton(): void {
        const btn = this.container.querySelector("#dia-apply-btn") as HTMLButtonElement;
        const warn = this.container.querySelector("#dia-tmdl-warning") as HTMLElement;
        if (btn) {
            btn.disabled = !this.tmdlLoaded;
        }
        if (warn) {
            warn.style.display = this.tmdlLoaded ? "none" : "block";
        }
    }

    private getSettingsUrl(): string {
        const raw = (this.container.querySelector("#dia-s-url") as HTMLInputElement).value.trim().replace(/\/+$/, "");
        // eslint-disable-next-line powerbi-visuals/no-http-string
        if (raw && !/^https?:\/\//i.test(raw)) return "http://" + raw;
        return raw;
    }

    private async applySettings(): Promise<void> {
        const applyBtn = this.container.querySelector("#dia-apply-btn") as HTMLButtonElement;
        const prevText = applyBtn.textContent;
        applyBtn.textContent = "Saving...";
        applyBtn.disabled = true;

        const url = (this.container.querySelector("#dia-s-url") as HTMLInputElement).value.trim();
        if (url) this.backendUrl = url;

        // Save license key
        const licKey = (this.container.querySelector("#dia-s-license") as HTMLInputElement).value.trim();
        this.licenseKey = licKey;

        // Send config + connections to backend
        if (this.backendUrl) {
            const config: any = {};

            const apiKey = (this.container.querySelector("#dia-s-apikey") as HTMLInputElement).value.trim();
            const model = (this.container.querySelector("#dia-s-model") as HTMLInputElement).value.trim();
            const extra = (this.container.querySelector("#dia-s-extra") as HTMLTextAreaElement).value.trim();

            if (apiKey) config.openrouter_api_key = apiKey;
            if (model) config.llm_model = model;
            if (extra) config.extra_context = extra;

            this.extraContext = extra;

            try {
                await this.authFetch(`${this.backendUrl}/config`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(config),
                });
            } catch (e) {
                // Silently fail — settings are still applied locally
            }

            this.readConnectionsFromUI();
            try {
                await this.authFetch(`${this.backendUrl}/connections`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ connections: this.connections }),
                });
            } catch (e) {
                // Silently fail
            }
        }

        applyBtn.textContent = prevText;
        applyBtn.disabled = false;
        this.lastBackendUrl = "";
        this.closeSettings();
        this.wakeAndVerify();
    }

    private async wakeAndVerify(): Promise<void> {
        const statusEl = this.container.querySelector("#dia-status") as HTMLElement;
        const dot = this.container.querySelector("#dia-conn-dot") as HTMLElement;
        if (!this.backendUrl || !statusEl) return;

        // Phase 1: Show checking state
        this.warehouseState = "CHECKING";
        this.renderStatusDot(statusEl);
        if (dot) dot.className = "dia-dot dia-dot-starting";

        try {
            // First check health to see if any connections are configured
            const healthResp = await fetch(`${this.backendUrl}/health`);
            if (healthResp.ok) {
                const health = await healthResp.json();
                if (health.databricks_connected) {
                    // Has connections — test the first Databricks one (wakes warehouse)
                    const resp = await fetch(`${this.backendUrl}/test-connection`, {
                        method: "POST",
                    });
                    const data = await resp.json();

                    if (data.status === "connected") {
                        this.warehouseState = "RUNNING";
                        this.lastBackendUrl = this.backendUrl;
                        this.renderStatusDot(statusEl);
                        if (dot) dot.className = "dia-dot dia-dot-on";
                        this.stopPolling();
                        return;
                    }

                    if (data.status === "starting" || data.state === "STOPPED" || data.state === "STARTING") {
                        this.warehouseState = data.state || "STARTING";
                        this.lastBackendUrl = this.backendUrl;
                        statusEl.innerHTML = `<span class="dia-dot dia-dot-starting"></span> Warehouse starting — waiting...`;
                        if (dot) dot.className = "dia-dot dia-dot-starting";
                        this.startWakePolling();
                        return;
                    }

                    // If test-connection returned an error but connections exist,
                    // they might all be SQL Server (no Databricks warehouse to wake)
                    if (data.detail && data.detail.includes("No Databricks")) {
                        // Only SQL Server connections — show connected
                        this.warehouseState = "RUNNING";
                        this.lastBackendUrl = this.backendUrl;
                        this.renderStatusDot(statusEl);
                        if (dot) dot.className = "dia-dot dia-dot-on";
                        this.stopPolling();
                        return;
                    }

                    this.warehouseState = "ERROR";
                    this.lastBackendUrl = this.backendUrl;
                    statusEl.innerHTML = `<span class="dia-dot dia-dot-error"></span> ${data.message || data.detail || "Connection failed"}`;
                    if (dot) dot.className = "dia-dot dia-dot-error";
                    return;
                }
            }

            // No connections configured
            this.warehouseState = "RUNNING";
            this.lastBackendUrl = this.backendUrl;
            statusEl.innerHTML = `<span class="dia-dot dia-dot-on"></span> Backend connected`;
            if (dot) dot.className = "dia-dot dia-dot-on";
        } catch (e: any) {
            this.warehouseState = "ERROR";
            this.lastBackendUrl = this.backendUrl;
            statusEl.innerHTML = `<span class="dia-dot dia-dot-error"></span> Cannot reach backend`;
            if (dot) dot.className = "dia-dot dia-dot-error";
        }
    }

    private startWakePolling(): void {
        this.stopPolling();
        let polls = 0;
        const statusEl = this.container.querySelector("#dia-status") as HTMLElement;
        const dot = this.container.querySelector("#dia-conn-dot") as HTMLElement;

        this.statusPollTimer = window.setInterval(async () => {
            polls++;
            if (polls > 60) {
                this.stopPolling();
                this.warehouseState = "ERROR";
                if (statusEl) statusEl.innerHTML = `<span class="dia-dot dia-dot-error"></span> Warehouse startup timed out`;
                if (dot) dot.className = "dia-dot dia-dot-error";
                return;
            }

            try {
                // Try the real connection test, not just state check
                const resp = await fetch(`${this.backendUrl}/test-connection`, {
                    method: "POST",
                });
                const data = await resp.json();

                if (data.status === "connected") {
                    this.stopPolling();
                    this.warehouseState = "RUNNING";
                    if (statusEl) this.renderStatusDot(statusEl);
                    if (dot) dot.className = "dia-dot dia-dot-on";
                    return;
                }

                // Still starting — update elapsed time
                if (statusEl) {
                    statusEl.innerHTML = `<span class="dia-dot dia-dot-starting"></span> Warehouse starting... (${polls * 5}s)`;
                }
            } catch (e) {
                // Transient error — keep polling
            }
        }, 5000) as unknown as number;
    }

    // ══════════════════════════════════════
    // POWER BI UPDATE (reads settings)
    // ══════════════════════════════════════
    public update(options: VisualUpdateOptions): void {
        this.host.eventService.renderingStarted(options);

        if (options && options.dataViews && options.dataViews[0]) {
            const dv = options.dataViews[0];
            this.fmtSettings = this.fmtService.populateFormattingSettingsModel(PBIChatFormattingSettings, dv);
            const url = this.fmtSettings.connection.backendUrl.value;
            if (url) this.backendUrl = url;
            const key = this.fmtSettings.connection.licenseKey.value;
            if (key) this.licenseKey = key;
        }

        // High contrast mode support
        this.applyHighContrast();

        this.updateStatus();
        this.host.eventService.renderingFinished(options);
    }

    private applyHighContrast(): void {
        const cp = this.host.colorPalette as any;
        const root = this.container.querySelector(".dia-root") as HTMLElement;
        if (!root) return;

        if (cp.isHighContrast) {
            const fg = cp.foreground?.value || "#ffffff";
            const bg = cp.background?.value || "#000000";
            const fg2 = cp.foregroundSelected?.value || fg;
            const hyper = cp.hyperlink?.value || fg;
            root.classList.add("dia-hc");
            root.style.setProperty("--bg", bg);
            root.style.setProperty("--bg2", bg);
            root.style.setProperty("--bg3", bg);
            root.style.setProperty("--bg4", bg);
            root.style.setProperty("--bgc", bg);
            root.style.setProperty("--tx", fg);
            root.style.setProperty("--tx2", fg);
            root.style.setProperty("--txm", fg);
            root.style.setProperty("--ac", hyper);
            root.style.setProperty("--bd", fg);
            root.style.setProperty("--bdi", fg);
            root.style.setProperty("--grn", fg2);
            root.style.setProperty("--red", fg2);
            root.style.setProperty("--user-label", hyper);
            root.style.setProperty("--user-text", fg);
            root.style.setProperty("--warn", fg2);
            root.style.setProperty("--primary", hyper);
        } else {
            root.classList.remove("dia-hc");
            // Remove inline overrides so CSS custom properties take effect
            const vars = ["--bg", "--bg2", "--bg3", "--bg4", "--bgc", "--tx", "--tx2", "--txm",
                "--ac", "--bd", "--bdi", "--grn", "--red", "--user-label", "--user-text", "--warn", "--primary"];
            vars.forEach(v => root.style.removeProperty(v));
        }
    }

    public getFormattingModel(): powerbi.visuals.FormattingModel {
        return this.fmtService.buildFormattingModel(this.fmtSettings);
    }

    // ══════════════════════════════════════
    // CLEAR CHAT
    // ══════════════════════════════════════
    private clearChat(): void {
        this.history = [];
        this.msgsEl.innerHTML = "";
        const welcome = document.createElement("div");
        welcome.className = "dia-welcome";
        welcome.id = "dia-welcome";
        const connected = this.backendUrl && this.warehouseState && this.warehouseState !== "ERROR";
        welcome.innerHTML = `
            <h1>PBIChat</h1>
            <p class="dia-welcome-sub">AI-powered data assistant with live SQL access</p>
            <div class="dia-setup-banner" id="dia-setup-banner" style="${connected ? "display:none" : ""}">
                <div class="dia-setup-title">Get Started</div>
                <div class="dia-setup-steps">
                    <div class="dia-setup-step"><span class="dia-step-num">1</span> Click <strong>Settings</strong> below and enter the admin password</div>
                    <div class="dia-setup-step"><span class="dia-step-num">2</span> Set your <strong>Backend API URL</strong> and <strong>API key</strong></div>
                    <div class="dia-setup-step"><span class="dia-step-num">3</span> Add a <strong>database connection</strong> (Databricks or SQL Server)</div>
                    <div class="dia-setup-step"><span class="dia-step-num">4</span> Upload your <strong>.tmdl files</strong> and click Apply</div>
                </div>
            </div>
            <div class="dia-sugs">
                <button class="dia-sug" data-q="What tables are in my database?">What tables are in my database?</button>
                <button class="dia-sug" data-q="Show me a summary of the data">Show me a summary of the data</button>
                <button class="dia-sug" data-q="Write a DAX measure for this metric">Write a DAX measure for this metric</button>
                <button class="dia-sug" data-q="What trends or anomalies exist?">What trends or anomalies exist?</button>
            </div>`;
        this.msgsEl.appendChild(welcome);
        this.welcomeEl = welcome;
        welcome.querySelectorAll(".dia-sug").forEach((btn) => {
            btn.addEventListener("click", (e) => {
                const q = (e.target as HTMLElement).getAttribute("data-q") || "";
                this.inputEl.value = q;
                this.send();
            });
        });
        this.inputEl.focus();
    }

    // ══════════════════════════════════════
    // LICENSE MANAGEMENT
    // ══════════════════════════════════════
    private async fetchLicenseStatus(): Promise<void> {
        if (!this.backendUrl) return;
        try {
            const resp = await this.authFetch(`${this.backendUrl}/license`);
            if (!resp.ok) return;
            const data = await resp.json();
            this.licenseTier = data.tier;
            this.dailyUsed = data.daily_used;
            this.dailyLimit = data.daily_limit;
            this.allowedCharts = data.allowed_charts;
            this.renderTierBadge();
        } catch (_) { /* backend unreachable */ }
    }

    private renderTierBadge(): void {
        const badge = this.container.querySelector("#dia-tier-badge") as HTMLElement;
        if (!badge) return;
        if (this.licenseTier === "pro") {
            badge.className = "dia-tier-badge dia-tier-pro";
            badge.textContent = "PRO";
        } else {
            const remaining = this.dailyLimit !== null ? this.dailyLimit - this.dailyUsed : null;
            badge.className = "dia-tier-badge dia-tier-free";
            badge.textContent = remaining !== null ? `FREE (${remaining}/${this.dailyLimit})` : "FREE";
        }
    }

    private showUpgradePrompt(): void {
        const el = document.createElement("div");
        el.className = "dia-upgrade-banner";
        el.innerHTML = `
            <div class="dia-upgrade-icon">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                </svg>
            </div>
            <div class="dia-upgrade-text">
                <strong>Daily query limit reached</strong>
                <p>You've used all ${this.dailyLimit} free queries for today.
                   Upgrade to Pro for unlimited queries, all chart types, and multiple connections.</p>
                <button class="dia-test-btn dia-btn-primary dia-upgrade-cta" id="dia-upgrade-banner-btn">Upgrade to Pro &mdash; $15/mo</button>
            </div>`;
        this.msgsEl.appendChild(el);
        el.querySelector("#dia-upgrade-banner-btn")?.addEventListener("click", () => this.initiateUpgrade());
        this.chatEl.scrollTop = this.chatEl.scrollHeight;
    }

    private async createLicense(): Promise<void> {
        const label = (this.container.querySelector("#dia-lic-label") as HTMLInputElement).value.trim();
        const resultEl = this.container.querySelector("#dia-lic-result") as HTMLElement;
        resultEl.style.display = "block";
        resultEl.className = "dia-test-result";
        resultEl.textContent = "Creating...";
        try {
            const resp = await this.authFetch(`${this.backendUrl}/admin/licenses`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label, tier: "pro" }),
            });
            if (!resp.ok) throw new Error((await resp.json()).detail || "Failed");
            const data = await resp.json();
            resultEl.className = "dia-test-result ok";
            resultEl.innerHTML = `Key: <code>${this.escapeHtml(data.key)}</code>`;
            this.loadLicenses();
        } catch (e: any) {
            resultEl.className = "dia-test-result fail";
            resultEl.textContent = e.message || "Failed to create license.";
        }
    }

    private async loadLicenses(): Promise<void> {
        const listEl = this.container.querySelector("#dia-lic-list") as HTMLElement;
        if (!listEl || !this.backendUrl) return;
        try {
            const resp = await this.authFetch(`${this.backendUrl}/admin/licenses`);
            if (!resp.ok) { listEl.innerHTML = ""; return; }
            const data = await resp.json();
            if (!data.licenses || data.licenses.length === 0) {
                listEl.innerHTML = '<div class="dia-hint">No license keys created yet.</div>';
                return;
            }
            listEl.innerHTML = data.licenses.map((lic: any) => `
                <div class="dia-conn-card" style="padding:8px 12px;margin-top:6px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <strong>${this.escapeHtml(lic.label || "Unnamed")}</strong>
                            <span class="dia-tier-badge ${lic.is_active ? "dia-tier-pro" : "dia-tier-free"}" style="margin-left:6px;">
                                ${lic.is_active ? lic.tier.toUpperCase() : "REVOKED"}
                            </span>
                            <div style="font-size:10px;color:var(--txm);margin-top:2px;">
                                ${this.escapeHtml(lic.key.substring(0, 12))}...
                                &middot; Created ${lic.created_at.substring(0, 10)}
                            </div>
                        </div>
                        ${lic.is_active ? `<button class="dia-conn-remove" data-lic-key="${this.escapeHtml(lic.key)}" title="Revoke">&times;</button>` : ""}
                    </div>
                </div>`).join("");

            listEl.querySelectorAll<HTMLButtonElement>(".dia-conn-remove[data-lic-key]").forEach(btn => {
                btn.addEventListener("click", () => this.revokeLicense(btn.dataset.licKey!));
            });
        } catch (_) { listEl.innerHTML = ""; }
    }

    private async revokeLicense(key: string): Promise<void> {
        try {
            await this.authFetch(`${this.backendUrl}/admin/licenses/${encodeURIComponent(key)}`, {
                method: "DELETE",
            });
            this.loadLicenses();
        } catch (_) { /* silently fail */ }
    }

    // ══════════════════════════════════════
    // CHAT LOGIC
    // ══════════════════════════════════════
    private async send(): Promise<void> {
        const text = this.inputEl.value.trim();
        if (!text || this.busy) return;

        if (!this.backendUrl) {
            this.showError("Backend API URL not set. Click Settings to configure.");
            return;
        }

        this.busy = true;
        this.sendBtn.disabled = true;
        this.inputEl.value = "";
        this.inputEl.style.height = "auto";

        this.addMessage("user", text);
        this.showTyping();

        try {
            // Pre-check warehouse state — show status banner while waiting
            let warehouseReady = false;
            try {
                const wsResp = await fetch(`${this.backendUrl}/warehouse-status`);
                if (wsResp.ok) {
                    const ws = await wsResp.json();
                    if (ws.ready) {
                        warehouseReady = true;
                    } else if (ws.state === "STARTING" || ws.state === "STOPPED") {
                        this.hideTyping();
                        this.showWarehouseStatus(ws.state, ws.name || "SQL Warehouse");
                        // Poll until ready, then continue
                        await this.waitForWarehouse();
                        this.hideWarehouseStatus();
                        this.showTyping();
                        warehouseReady = true;
                    }
                }
            } catch (_) { /* proceed to chat anyway */ }

            const resp = await this.authFetch(`${this.backendUrl}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message: text,
                    history: this.history.slice(-20),
                    extra_context: this.extraContext || null,
                }),
            });

            this.hideTyping();

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `API error ${resp.status}`);
            }

            const data = await resp.json();

            // Update license tier state from response
            if (data.tier) this.licenseTier = data.tier;
            if (data.daily_used != null) this.dailyUsed = data.daily_used;
            if (data.daily_limit !== undefined) this.dailyLimit = data.daily_limit;
            this.renderTierBadge();

            this.history.push({ role: "user", content: text });
            this.history.push({ role: "assistant", content: data.response });

            // Show upgrade prompt if limit reached, otherwise show normal response
            if (data.tier === "free" && data.daily_limit != null && data.daily_used >= data.daily_limit) {
                this.showUpgradePrompt();
            } else {
                this.addMessage("ai", data.response);
            }
        } catch (e: any) {
            this.hideTyping();
            this.showError(e.message || "Request failed.");
        }

        this.busy = false;
        this.sendBtn.disabled = false;
        this.inputEl.focus();
    }

    // ══════════════════════════════════════
    // UI HELPERS
    // ══════════════════════════════════════
    private addMessage(role: "user" | "ai", content: string): void {
        if (this.welcomeEl && this.welcomeEl.parentNode) {
            this.welcomeEl.remove();
        }

        const el = document.createElement("div");
        el.className = `dia-msg dia-${role}`;

        const label = role === "user" ? "You" : "PBIChat";
        const body = role === "ai" ? this.formatMarkdown(content) : this.escapeHtml(content);

        el.innerHTML = `<div class="dia-msg-label">${label}</div><div class="dia-msg-text">${body}</div>`;
        this.msgsEl.appendChild(el);

        // Render any chart blocks that were inserted
        if (role === "ai") {
            this.renderCharts(el);
        }

        this.chatEl.scrollTop = this.chatEl.scrollHeight;
    }

    private showTyping(): void {
        const el = document.createElement("div");
        el.className = "dia-msg dia-ai";
        el.id = "dia-typing";
        el.innerHTML = `<div class="dia-msg-label">PBIChat</div><div class="dia-msg-text"><div class="dia-dots"><span></span><span></span><span></span></div></div>`;
        this.msgsEl.appendChild(el);
        this.chatEl.scrollTop = this.chatEl.scrollHeight;
    }

    private hideTyping(): void {
        const el = this.container.querySelector("#dia-typing");
        if (el) el.remove();
    }

    private showError(rawMsg: string): void {
        // Map common technical errors to user-friendly messages
        let msg = rawMsg;
        if (/fetch|network|ERR_CONNECTION|ECONNREFUSED/i.test(msg)) {
            msg = "Cannot reach the backend server. Please check your Backend API URL in Settings.";
        } else if (/403|Invalid password/i.test(msg)) {
            msg = "Authentication failed. Please re-enter your password in Settings.";
        } else if (/429|rate limit/i.test(msg)) {
            msg = "You're sending messages too quickly. Please wait a moment and try again.";
        } else if (/500|Internal Server/i.test(msg)) {
            msg = "The server encountered an error. Please try again or check the backend logs.";
        } else if (/502|LLM API error/i.test(msg)) {
            msg = "The AI service is temporarily unavailable. Please try again in a moment.";
        } else if (/OpenRouter.*not configured/i.test(msg)) {
            msg = "The AI API key is not configured. Please set it in Settings.";
        }

        const el = document.createElement("div");
        el.className = "dia-error";
        el.textContent = msg;
        this.msgsEl.appendChild(el);
        this.chatEl.scrollTop = this.chatEl.scrollHeight;
    }

    private showWarehouseStatus(state: string, name: string): void {
        this.hideWarehouseStatus();
        const el = document.createElement("div");
        el.className = "dia-wh-status";
        el.id = "dia-wh-status";
        const stateLabel = state === "STARTING" ? "Starting up" : "Waking up";
        el.innerHTML = [
            `<div class="dia-wh-spinner"></div>`,
            `<div class="dia-wh-info">`,
            `<div class="dia-wh-title">${this.escapeHtml(name)} is ${stateLabel.toLowerCase()}...</div>`,
            `<div class="dia-wh-sub">This usually takes 2–5 minutes. Your query will run automatically once ready.</div>`,
            `</div>`,
        ].join("");
        this.msgsEl.appendChild(el);
        this.chatEl.scrollTop = this.chatEl.scrollHeight;
    }

    private hideWarehouseStatus(): void {
        const el = this.container.querySelector("#dia-wh-status");
        if (el) el.remove();
    }

    private waitForWarehouse(): Promise<void> {
        return new Promise((resolve) => {
            let polls = 0;
            const iv = window.setInterval(async () => {
                polls++;
                if (polls > 60) { clearInterval(iv); resolve(); return; } // 5 min timeout
                try {
                    const r = await fetch(`${this.backendUrl}/warehouse-status`);
                    if (r.ok) {
                        const ws = await r.json();
                        if (ws.ready) { clearInterval(iv); resolve(); }
                    }
                } catch (_) { /* keep polling */ }
            }, 5000);
        });
    }

    // ══════════════════════════════════════
    // WAREHOUSE STATUS
    // ══════════════════════════════════════
    private updateStatus(): void {
        const el = this.container.querySelector("#dia-status") as HTMLElement;
        if (!el) return;

        // Update connection dot in settings button
        const dot = this.container.querySelector("#dia-conn-dot") as HTMLElement;

        // Show/hide setup banner based on connection state
        const banner = this.container.querySelector("#dia-setup-banner") as HTMLElement;
        if (banner) {
            const connected = this.backendUrl && this.warehouseState && this.warehouseState !== "ERROR";
            banner.style.display = connected ? "none" : "";
        }

        if (!this.backendUrl) {
            el.innerHTML = `<span class="dia-dot"></span> Not connected`;
            if (dot) dot.className = "dia-dot";
            this.stopPolling();
            return;
        }

        if (this.backendUrl !== this.lastBackendUrl) {
            this.lastBackendUrl = this.backendUrl;
            this.checkWarehouseStatus();
            this.fetchLicenseStatus();
            return;
        }

        this.renderStatusDot(el);
        if (dot) {
            dot.className = this.warehouseState === "RUNNING" ? "dia-dot dia-dot-on"
                : (this.warehouseState === "STARTING" || this.warehouseState === "STOPPED") ? "dia-dot dia-dot-starting"
                : this.warehouseState === "ERROR" ? "dia-dot dia-dot-error"
                : "dia-dot";
        }
    }

    private renderStatusDot(el: HTMLElement): void {
        switch (this.warehouseState) {
            case "RUNNING":
                el.innerHTML = `<span class="dia-dot dia-dot-on"></span> Connected and ready`;
                break;
            case "STARTING":
                el.innerHTML = `<span class="dia-dot dia-dot-starting"></span> Warehouse starting up...`;
                break;
            case "STOPPED":
                el.innerHTML = `<span class="dia-dot dia-dot-starting"></span> Warehouse stopped — starting...`;
                break;
            case "CHECKING":
                el.innerHTML = `<span class="dia-dot"></span> Checking connection...`;
                break;
            case "ERROR":
                el.innerHTML = `<span class="dia-dot dia-dot-error"></span> Cannot reach backend`;
                break;
            default:
                el.innerHTML = `<span class="dia-dot dia-dot-on"></span> Connected`;
                break;
        }
    }

    private async checkWarehouseStatus(): Promise<void> {
        const el = this.container.querySelector("#dia-status") as HTMLElement;
        if (!el || !this.backendUrl) return;

        this.warehouseState = "CHECKING";
        this.renderStatusDot(el);

        try {
            const resp = await fetch(
                `${this.backendUrl}/warehouse-status`
            );
            if (!resp.ok) {
                this.warehouseState = "CONNECTED";
                this.renderStatusDot(el);
                return;
            }

            const data = await resp.json();
            this.warehouseState = data.state;
            this.renderStatusDot(el);
            this.updateStatus(); // update the dot on the settings button

            if (data.state === "STARTING" || data.state === "STOPPED") {
                this.startPolling();
            } else {
                this.stopPolling();
            }
        } catch (e) {
            this.warehouseState = "ERROR";
            this.renderStatusDot(el);
            this.updateStatus();
        }
    }

    private startPolling(): void {
        this.stopPolling();
        let polls = 0;
        const maxPolls = 60;

        this.statusPollTimer = window.setInterval(async () => {
            polls++;
            if (polls > maxPolls) {
                this.stopPolling();
                const el = this.container.querySelector("#dia-status") as HTMLElement;
                if (el) el.innerHTML = `<span class="dia-dot dia-dot-error"></span> Warehouse startup timed out`;
                return;
            }

            try {
                const resp = await fetch(
                    `${this.backendUrl}/warehouse-status`
                );
                if (resp.ok) {
                    const data = await resp.json();
                    this.warehouseState = data.state;
                    const el = this.container.querySelector("#dia-status") as HTMLElement;
                    if (el) this.renderStatusDot(el);
                    this.updateStatus();

                    if (data.state === "RUNNING") {
                        this.stopPolling();
                    }
                }
            } catch (e) {
                // Ignore transient errors during polling
            }
        }, 5000) as unknown as number;
    }

    private stopPolling(): void {
        if (this.statusPollTimer !== null) {
            clearInterval(this.statusPollTimer);
            this.statusPollTimer = null;
        }
    }

    private escapeHtml(text: string): string {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // ══════════════════════════════════════
    // CHART RENDERING
    // ══════════════════════════════════════
    private static CHART_COLORS = [
        { solid: "#c4956a", light: "#c4956aE6", glow: "#c4956a80" },
        { solid: "#7cacf8", light: "#7cacf8E6", glow: "#7cacf880" },
        { solid: "#4ade80", light: "#4ade80E6", glow: "#4ade8080" },
        { solid: "#a78bfa", light: "#a78bfaE6", glow: "#a78bfa80" },
        { solid: "#fb923c", light: "#fb923cE6", glow: "#fb923c80" },
        { solid: "#22d3ee", light: "#22d3eeE6", glow: "#22d3ee80" },
        { solid: "#f87171", light: "#f87171E6", glow: "#f8717180" },
        { solid: "#e879f9", light: "#e879f9E6", glow: "#e879f980" },
        { solid: "#facc15", light: "#facc15E6", glow: "#facc1580" },
        { solid: "#34d399", light: "#34d399E6", glow: "#34d39980" },
    ];

    private chartCounter: number = 0;
    private chartDataMap: Map<string, string> = new Map();

    private createGradient(ctx: CanvasRenderingContext2D, color: typeof PBIChat.CHART_COLORS[0], vertical: boolean = true): CanvasGradient {
        const h = ctx.canvas.height || 300;
        const w = ctx.canvas.width || 600;
        const grad = vertical
            ? ctx.createLinearGradient(0, 0, 0, h)
            : ctx.createLinearGradient(0, 0, w, 0);
        grad.addColorStop(0, color.light);
        grad.addColorStop(1, color.glow);
        return grad;
    }

    private renderCharts(container: HTMLElement): void {
        const canvases = container.querySelectorAll<HTMLCanvasElement>("canvas[data-chart-id]");
        canvases.forEach((canvas) => {
            const chartId = canvas.getAttribute("data-chart-id");
            if (!chartId) return;
            const json = this.chartDataMap.get(chartId);
            if (!json) return;

            try {
                const spec = JSON.parse(json);
                // Enforce chart type limits — downgrade to bar if not allowed
                if (this.allowedCharts.length > 0 && !this.allowedCharts.includes(spec.type)) {
                    spec.type = "bar";
                }
                const ctx = canvas.getContext("2d")!;
                const isRadial = spec.type === "pie" || spec.type === "doughnut";
                const isHorizontal = spec.type === "horizontalBar";
                const ct = this.chartTheme;

                const datasets = (spec.datasets || []).map((ds: any, i: number) => {
                    const palette = PBIChat.CHART_COLORS[i % PBIChat.CHART_COLORS.length];
                    const base: any = {
                        label: ds.label || `Series ${i + 1}`,
                        data: ds.data,
                    };

                    if (isRadial) {
                        base.backgroundColor = (spec.labels || []).map((_: any, j: number) => {
                            const p = PBIChat.CHART_COLORS[j % PBIChat.CHART_COLORS.length];
                            return p.solid + "E6";
                        });
                        base.hoverBackgroundColor = (spec.labels || []).map((_: any, j: number) => {
                            const p = PBIChat.CHART_COLORS[j % PBIChat.CHART_COLORS.length];
                            return p.solid;
                        });
                        base.borderColor = ct.pieBorder;
                        base.borderWidth = 2;
                        base.hoverBorderColor = ct.pieHoverBorder;
                        if (spec.type === "doughnut") {
                            base.borderRadius = 4;
                            base.spacing = 3;
                        }
                    } else if (spec.type === "line") {
                        const fillGrad = this.createGradient(ctx, palette);
                        base.borderColor = palette.solid;
                        base.backgroundColor = fillGrad;
                        base.borderWidth = 2.5;
                        base.pointRadius = 0;
                        base.pointHoverRadius = 6;
                        base.pointHoverBackgroundColor = palette.solid;
                        base.pointHoverBorderColor = ct.pointBorder;
                        base.pointHoverBorderWidth = 2;
                        base.tension = 0.4;
                        base.fill = true;
                    } else if (spec.type === "scatter") {
                        base.backgroundColor = palette.solid + "CC";
                        base.borderColor = palette.solid;
                        base.borderWidth = 1.5;
                        base.pointRadius = 5;
                        base.pointHoverRadius = 7;
                        base.pointHoverBackgroundColor = palette.solid;
                        base.pointHoverBorderColor = ct.pointBorder;
                        base.pointHoverBorderWidth = 2;
                    } else {
                        // Bar charts — gradient fill
                        const fillGrad = this.createGradient(ctx, palette, !isHorizontal);
                        base.backgroundColor = fillGrad;
                        base.hoverBackgroundColor = palette.solid + "DD";
                        base.borderColor = palette.solid;
                        base.borderWidth = 0;
                        base.borderRadius = 6;
                        base.borderSkipped = false;
                        base.maxBarThickness = 48;
                    }
                    return base;
                });

                const chartType = isHorizontal ? "bar" : spec.type;

                new Chart(canvas, {
                    type: chartType,
                    data: {
                        labels: spec.labels || [],
                        datasets: datasets,
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        indexAxis: isHorizontal ? "y" as const : "x" as const,
                        animation: {
                            duration: 700,
                            easing: "easeOutQuart" as const,
                        },
                        layout: {
                            padding: { top: 4, bottom: 4, left: 2, right: 2 },
                        },
                        plugins: {
                            title: {
                                display: !!spec.title,
                                text: spec.title || "",
                                color: ct.text,
                                font: { size: 13, weight: "600" as const, family: "'Inter', 'Segoe UI', sans-serif" },
                                padding: { top: 0, bottom: 16 },
                            },
                            legend: {
                                display: datasets.length > 1 || isRadial,
                                position: "bottom" as const,
                                labels: {
                                    color: ct.textSec,
                                    font: { size: 11, family: "'Inter', 'Segoe UI', sans-serif" },
                                    usePointStyle: true,
                                    pointStyle: "circle" as const,
                                    boxWidth: 6,
                                    boxHeight: 6,
                                    padding: 16,
                                },
                            },
                            tooltip: {
                                backgroundColor: ct.tooltipBg,
                                titleColor: ct.text,
                                bodyColor: ct.tooltipBody,
                                titleFont: { size: 12, weight: "600" as const, family: "'Inter', sans-serif" },
                                bodyFont: { size: 11, family: "'Inter', sans-serif" },
                                borderColor: ct.tooltipBorder,
                                borderWidth: 1,
                                cornerRadius: 10,
                                padding: { top: 10, bottom: 10, left: 14, right: 14 },
                                boxPadding: 6,
                                usePointStyle: true,
                                displayColors: datasets.length > 1,
                            },
                        },
                        scales: isRadial ? {} : {
                            x: {
                                ticks: {
                                    color: ct.textMuted,
                                    font: { size: 10, family: "'Inter', sans-serif" },
                                    padding: 8,
                                    maxRotation: 45,
                                },
                                grid: {
                                    color: ct.grid,
                                    lineWidth: 0.5,
                                },
                                border: { display: false },
                            },
                            y: {
                                ticks: {
                                    color: ct.textMuted,
                                    font: { size: 10, family: "'Inter', sans-serif" },
                                    padding: 8,
                                },
                                grid: {
                                    color: ct.grid,
                                    lineWidth: 0.5,
                                },
                                border: { display: false },
                            },
                        },
                        ...(isRadial && spec.type === "doughnut" ? { cutout: "65%" } : {}),
                    },
                });
            } catch (e) {
                canvas.parentElement!.innerHTML = `<div class="dia-error">Failed to render chart: ${(e as Error).message}</div>`;
            }
        });
    }

    private formatMarkdown(text: string): string {
        let t = text;
        // Extract chart/code blocks before escaping HTML (they need raw content)
        const CHART_PLACEHOLDER = "%%CHART%%";
        const CODE_PLACEHOLDER = "%%CODE%%";
        const chartHtmls: string[] = [];
        const codeBlocks: string[] = [];
        t = t.replace(/```chart\n([\s\S]*?)```/g, (_match, json) => {
            const id = `dia-chart-${++this.chartCounter}`;
            this.chartDataMap.set(id, json.trim());
            chartHtmls.push(`<div class="dia-chart-wrap"><canvas id="${id}" data-chart-id="${id}"></canvas></div>`);
            return CHART_PLACEHOLDER;
        });
        t = t.replace(/```sql_exec\n([\s\S]*?)```/g, "```sql\n$1```");
        // Extract code blocks before escaping
        t = t.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) => {
            codeBlocks.push(`<pre><code>${this.escapeHtml(code)}</code></pre>`);
            return CODE_PLACEHOLDER;
        });
        // Escape HTML in remaining text to prevent XSS from LLM output
        t = t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        // Restore code blocks
        while (t.includes(CODE_PLACEHOLDER)) {
            t = t.replace(CODE_PLACEHOLDER, codeBlocks.shift()!);
        }
        t = t.replace(/`([^`]+)`/g, (_m, code) => `<code>${this.escapeHtml(code)}</code>`);
        t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        t = t.replace(/\*(.+?)\*/g, "<em>$1</em>");
        t = t.replace(/^[\-\*] (.+)$/gm, "<li>$1</li>");
        t = t.replace(/^\d+\.\s+(.+)$/gm, "<li>$1</li>");
        t = t.replace(/^### (.+)$/gm, '<strong class="dia-h3">$1</strong>');
        t = t.replace(/^## (.+)$/gm, '<strong class="dia-h2">$1</strong>');
        t = t.replace(/\n/g, "<br>");
        t = t.replace(/<br><(ul|ol|pre)/g, "<$1");
        t = t.replace(/<\/(ul|ol|pre)><br>/g, "</$1>");

        // Group consecutive chart placeholders into side-by-side rows (pairs of 2)
        if (chartHtmls.length >= 2) {
            // Build a map: find runs of consecutive placeholders and pair them
            let chartIdx = 0;
            const lines = t.split(CHART_PLACEHOLDER);
            let result = "";
            // lines[0] is text before first chart, lines[i] is text after chart i-1
            // We need to identify consecutive charts (where the text between them is only whitespace/br)
            const pending: number[] = [];
            for (let i = 0; i < lines.length; i++) {
                const isLast = i === lines.length - 1;
                if (isLast) {
                    // Flush any pending charts before appending final text
                    if (pending.length >= 2) {
                        for (let p = 0; p < pending.length; p += 2) {
                            if (p + 1 < pending.length) {
                                result += `<div class="dia-chart-row">${chartHtmls[pending[p]]}${chartHtmls[pending[p + 1]]}</div>`;
                            } else {
                                result += chartHtmls[pending[p]];
                            }
                        }
                    } else if (pending.length === 1) {
                        result += chartHtmls[pending[0]];
                    }
                    pending.length = 0;
                    result += lines[i];
                } else {
                    const betweenText = lines[i].replace(/<br\s*\/?>/g, "").trim();
                    if (pending.length === 0) {
                        // First chart in a potential run
                        result += lines[i];
                        pending.push(chartIdx);
                    } else if (betweenText === "") {
                        // Consecutive chart — keep accumulating
                        pending.push(chartIdx);
                    } else {
                        // Non-empty text breaks the run — flush pending
                        if (pending.length >= 2) {
                            for (let p = 0; p < pending.length; p += 2) {
                                if (p + 1 < pending.length) {
                                    result += `<div class="dia-chart-row">${chartHtmls[pending[p]]}${chartHtmls[pending[p + 1]]}</div>`;
                                } else {
                                    result += chartHtmls[pending[p]];
                                }
                            }
                        } else {
                            result += chartHtmls[pending[0]];
                        }
                        pending.length = 0;
                        result += lines[i];
                        pending.push(chartIdx);
                    }
                    chartIdx++;
                }
            }
            t = result;
        } else if (chartHtmls.length === 1) {
            // Single chart — just inline it
            t = t.replace(CHART_PLACEHOLDER, chartHtmls[0]);
        }

        return t;
    }
}
