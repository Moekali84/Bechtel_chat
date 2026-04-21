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

class PBIChatFormattingSettings extends formattingSettings.Model {
    cards: formattingSettings.SimpleCard[] = [];
}

export class PBIChat implements IVisual {
    private host: IVisualHost;
    private container: HTMLElement;
    private _backendUrl: string = "http://localhost:8000";
    private history: HistoryMsg[] = [];
    private fmtSettings = new PBIChatFormattingSettings();
    private fmtService: FormattingSettingsService;

    private get backendUrl(): string { return this._backendUrl; }
    private busy: boolean = false;
    private isDarkTheme: boolean = true;
    private extraContext: string = "";
    private tmdlLoaded: boolean = false;
    private pendingTmdlFiles: { name: string; content: string }[] = [];
    private connections: Array<{
        id: string; name: string; type: string;
        host?: string; http_path?: string; token?: string; catalog_schema?: string;
        server?: string; database?: string; username?: string; password?: string;
        _tokenSaved?: boolean; _passwordSaved?: boolean;
        _tokenPreview?: string;
    }> = [];

    // Warehouse status tracking
    private warehouseState: string = "";
    private statusPollTimer: number | null = null;

    // All available chart types
    private readonly ALL_CHARTS: string[] = ["bar", "line", "pie", "doughnut", "scatter", "horizontalBar"];

    // Data mode: "auto" picks based on available data, "inline" forces inline, "database" forces database
    private dataMode: "auto" | "inline" | "database" = "auto";

    // Size of the loaded TMDL on the backend (KB), for display in the settings panel.
    private tmdlSizeKb: number = 0;

    // Inline data mode (columns dropped into field well)
    private inlineDataCsv: string = "";
    private inlineStats: string = "";  // JSON summary stats from ALL rows
    private inlineColumnCount: number = 0;
    private inlineRowCount: number = 0;
    private inlineRowsSent: number = 0; // rows actually included in CSV (may be < total)
    private inlineTruncated: boolean = false;

    // DOM references
    private chatEl: HTMLElement;
    private msgsEl: HTMLElement;
    private inputEl: HTMLTextAreaElement;
    private sendBtn: HTMLButtonElement;
    private welcomeEl: HTMLElement;

    constructor(options: VisualConstructorOptions) {
        this.host = options.host;
        this.fmtService = new FormattingSettingsService();
        this.container = options.element;
        this.container.innerHTML = "";
        this.buildUI();
        this.wakeAndVerify();
        // Kick off backend state sync immediately so tmdlLoaded is populated
        // before the user has a chance to press Send. Power BI may not call
        // update() right away after a page switch, so we don't rely on it.
        if (this.backendUrl) this.refreshBackendState();
    }

    // ======================================
    // BUILD THE CHAT UI
    // ======================================
    private buildUI(): void {
        this.container.innerHTML = `
        <div class="dia-root">
            <div class="dia-chat" id="dia-chat">
                <div class="dia-msgs" id="dia-msgs">
            <div class="dia-logo" id="dia-logo">
                <div class="dia-logo-tagline">Bechtel AI Powered Technologies<br/>NS&amp;E PIIM Team<br/><span class="dia-support-line">Support : Moe Al Khalili ; Malkhalili@bechtel.com</span></div>
            </div>
                    <div class="dia-welcome" id="dia-welcome">
                        <div class="dia-setup-banner" id="dia-setup-banner">
                            <div class="dia-setup-title">Get Started</div>
                            <div class="dia-setup-steps">
                                <div class="dia-setup-step"><span class="dia-step-num">1</span> Click <strong>Settings</strong> and enter the password</div>
                                <div class="dia-setup-step"><span class="dia-step-num">2</span> Add a <strong>database connection</strong> (Databricks or SQL Server)</div>
                                <div class="dia-setup-step"><span class="dia-step-num">3</span> Select your <strong>semantic model folder</strong> (.tmdl files)</div>
                                <div class="dia-setup-step"><span class="dia-step-num">4</span> Click <strong>Apply &amp; Close</strong> to save</div>
                            </div>
                        </div>
                        <div class="dia-sugs">
                            <button class="dia-sug" data-q="What tables are in my database?">What tables are in my database?</button>
                            <button class="dia-sug" data-q="Show me a summary of the data">Show me a summary of the data</button>
                            <button class="dia-sug" data-q="Write a DAX measure for this metric">Write a DAX measure for this metric</button>
                            <button class="dia-sug" data-q="Show me the latest incidents for NS&amp;E projects for the last 7 days">Show me the latest incidents for NS&amp;E projects for the last 7 days</button>
                        </div>
                    </div>
                </div>
            </div>
            <div class="dia-input-wrap">
                <div class="dia-mode-toggle" id="dia-mode-toggle">
                    <button class="dia-mode-btn active" data-mode="auto">Auto</button>
                    <button class="dia-mode-btn" data-mode="inline">Inline Data</button>
                    <button class="dia-mode-btn" data-mode="database">Database</button>
                </div>
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

            <!-- Settings flyout -->
            <div class="dia-settings-overlay" id="dia-settings-overlay"></div>
            <div class="dia-settings" id="dia-settings">
                <div class="dia-settings-header">
                    <h3>Settings</h3>
                    <button class="dia-settings-close" id="dia-settings-close">&#x2715;</button>
                </div>
                <div class="dia-settings-body">
                    <div class="dia-settings-section">Backend Server</div>
                    <div class="dia-field">
                        <label>Backend URL</label>
                        <select id="dia-backend-url-select" class="dia-select">
                            <option value="http://localhost:8000">Local (localhost:8000)</option>
                            <option value="custom">Custom URL...</option>
                        </select>
                        <input type="text" id="dia-backend-url-custom" class="dia-input" placeholder="https://your-server.com" style="display:none; margin-top:6px;" />
                    </div>

                    <div class="dia-settings-section">AI API Configuration</div>
                    <div class="dia-field">
                        <label>LLM Preset</label>
                        <select id="dia-llm-preset-select" class="dia-select">
                            <option value="default">Default (Azure OpenAI)</option>
                        </select>
                        <div class="dia-preset-actions">
                            <button class="dia-test-btn" id="dia-save-preset-btn">Save as New</button>
                            <button class="dia-test-btn dia-btn-danger" id="dia-delete-preset-btn" style="display:none">Delete</button>
                        </div>
                    </div>
                    <div class="dia-field">
                        <label>Preset Name</label>
                        <input type="text" id="dia-preset-name" placeholder="My Custom LLM" class="dia-input"/>
                        <div class="dia-hint">Name for the new preset when saving.</div>
                    </div>
                    <div class="dia-field">
                        <label>API Endpoint</label>
                        <input type="text" id="dia-api-endpoint" placeholder="https://api.openrouter.com/v1/chat/completions" class="dia-input"/>
                        <div class="dia-hint">Leave empty to use default Azure OpenAI. Enter any OpenRouter API URL (e.g. https://api.openrouter.com/v1/chat/completions or https://openrouter.ai/api/v1/chat/completions).</div>
                    </div>
                    <div class="dia-field">
                        <label>API Key</label>
                        <input type="password" id="dia-api-key" placeholder="sk-or-v1-..." class="dia-input"/>
                        <div class="dia-hint">Your API key for the configured endpoint. Leave empty to use default.</div>
                    </div>
                    <div class="dia-field">
                        <label>LLM Model</label>
                        <input type="text" id="dia-llm-model" placeholder="gpt-4o-mini" class="dia-input"/>
                        <div class="dia-hint">Model name (e.g., gpt-4o-mini, anthropic/claude-3-haiku). Leave empty for default.</div>
                    </div>

                    <div class="dia-settings-section dia-collapsible" id="dia-conn-header">
                        <span>Data Connections</span>
                        <span class="dia-collapse-icon" id="dia-conn-collapse-icon">▾</span>
                    </div>
                    <div id="dia-conn-body">
                        <div id="dia-conn-list"></div>
                        <button class="dia-test-btn" id="dia-add-conn-btn">+ Add Connection</button>
                    </div>

                    <div class="dia-settings-section">Semantic Model (.tmdl Files)</div>
                    <div class="dia-tmdl-status" id="dia-tmdl-status">No semantic model uploaded yet.</div>
                    <div class="dia-tmdl-actions">
                        <button class="dia-test-btn" id="dia-add-tmdl-btn">Select Folder</button>
                        <button class="dia-test-btn dia-btn-muted" id="dia-tmdl-clear-btn" style="display:none">Clear Files</button>
                        <button class="dia-test-btn dia-btn-danger" id="dia-tmdl-delete-btn" style="display:none">Delete Saved Model</button>
                        <input type="file" id="dia-tmdl-file-input" webkitdirectory style="display:none"/>
                    </div>
                    <div class="dia-hint">Select the semantic model folder. All .tmdl files inside it (including subfolders) will be uploaded and replace the previously saved model.</div>
                    <div class="dia-tmdl-file-list" id="dia-tmdl-file-list" style="display:none"></div>
                    <div class="dia-test-result" id="dia-tmdl-result"></div>

                    <div class="dia-settings-section">Additional Context</div>
                    <div class="dia-field">
                        <label>Business rules, notes, or extra schema</label>
                        <textarea id="dia-s-extra" rows="3" placeholder="e.g. OSHA rates use 200,000 multiplier."></textarea>
                    </div>

                    <div class="dia-tmdl-warning" id="dia-tmdl-warning">Upload TMDL files before applying settings.</div>
                    <button class="dia-apply-btn" id="dia-apply-btn" disabled>Apply &amp; Close</button>
                </div>
            </div>

            <!-- Confirm dialog -->
            <div class="dia-confirm-overlay" id="dia-confirm-overlay"></div>
            <div class="dia-confirm-dialog" id="dia-confirm-dialog">
                <div class="dia-confirm-msg" id="dia-confirm-msg"></div>
                <div class="dia-confirm-btns">
                    <button class="dia-test-btn" id="dia-confirm-cancel">Cancel</button>
                    <button class="dia-test-btn dia-btn-danger" id="dia-confirm-ok">Delete</button>
                </div>
            </div>

            <!-- Help modal -->
            <div class="dia-help-overlay" id="dia-help-overlay"></div>
            <div class="dia-help-modal" id="dia-help-modal">
                <div class="dia-help-header">
                    <h3>Getting Started with PBIChat</h3>
                    <button class="dia-settings-close" id="dia-help-close">&#x2715;</button>
                </div>
                <div class="dia-help-body">
                    <div class="dia-help-section">
                        <strong class="dia-help-subtitle">Two Ways to Use PBIChat</strong>
                        <p style="margin:6px 0 12px;opacity:0.8">PBIChat works in two modes. Choose whichever fits your needs — or use both in the same report.</p>
                    </div>

                    <div class="dia-help-section">
                        <strong class="dia-help-subtitle">Mode 1: Inline Data (Quick Start — No Setup)</strong>
                        <p style="margin:4px 0 8px;opacity:0.7">Drag columns from your Power BI data model and start chatting instantly.</p>
                        <div class="dia-help-step">
                            <span class="dia-help-num">1</span>
                            <div>
                                <strong>Drag Columns</strong>
                                <p>In the <strong>Build</strong> pane (Visualizations), drag any columns or measures into the <strong>Columns</strong> field well — just like you would with a Table visual.</p>
                            </div>
                        </div>
                        <div class="dia-help-step">
                            <span class="dia-help-num">2</span>
                            <div>
                                <strong>Ask Questions</strong>
                                <p>The status bar will show your data size (e.g. "5 cols \u00d7 2,000 rows"). Start asking questions — the AI analyzes your data directly and responds with charts, tables, and insights.</p>
                            </div>
                        </div>
                        <div class="dia-help-step">
                            <span class="dia-help-num">\u2139</span>
                            <div>
                                <strong>How It Works</strong>
                                <p>PBIChat reads all the rows Power BI provides, computes accurate summary statistics (totals, averages, min/max) from the <em>full</em> dataset, and sends the data to the AI. Even with large datasets, aggregate answers (totals, counts, averages) are always accurate.</p>
                            </div>
                        </div>
                    </div>

                    <div class="dia-help-divider"></div>

                    <div class="dia-help-section">
                        <strong class="dia-help-subtitle">Mode 2: Database Connection (Full Power)</strong>
                        <p style="margin:4px 0 8px;opacity:0.7">Connect directly to your database for live SQL queries across your entire data warehouse.</p>
                        <div class="dia-help-step">
                            <span class="dia-help-num">1</span>
                            <div>
                                <strong>Open Settings</strong>
                                <p>Click <strong>Settings</strong> and enter the password to access configuration.</p>
                            </div>
                        </div>
                        <div class="dia-help-step">
                            <span class="dia-help-num">2</span>
                            <div>
                                <strong>Add a Data Connection</strong>
                                <p>In Settings, click <strong>+ Add Connection</strong>. Choose your database type:</p>
                                <ul>
                                    <li><strong>Databricks</strong> — Workspace host URL, SQL warehouse HTTP path, access token, and catalog.schema</li>
                                    <li><strong>SQL Server</strong> — Host, database name, username, and password</li>
                                </ul>
                                <p>Click <strong>Test</strong> to verify the connection works.</p>
                            </div>
                        </div>
                        <div class="dia-help-step">
                            <span class="dia-help-num">3</span>
                            <div>
                                <strong>Upload Your Semantic Model</strong>
                                <p>Your semantic model tells PBIChat about your tables, columns, and relationships.</p>
                                <ul>
                                    <li>In Power BI Desktop, right-click your model and choose <strong>Edit TMDL</strong></li>
                                    <li>In Settings, click <strong>Select Folder</strong> to import your <code>.tmdl</code> files</li>
                                </ul>
                            </div>
                        </div>
                        <div class="dia-help-step">
                            <span class="dia-help-num">4</span>
                            <div>
                                <strong>Ask Questions</strong>
                                <p>Click <strong>Apply &amp; Close</strong>, then ask questions in natural language. PBIChat queries your database live and returns results with charts, tables, and metric cards.</p>
                            </div>
                        </div>
                        <div class="dia-help-step">
                            <span class="dia-help-num">\u2139</span>
                            <div>
                                <strong>How It Works</strong>
                                <p>The AI reads your semantic model to understand table relationships, generates SQL queries, executes them against your database, and presents the results visually. It can chain multiple queries to answer complex questions. You never see the SQL — only the polished answer.</p>
                            </div>
                        </div>
                    </div>

                    <div class="dia-help-divider"></div>
                    <div class="dia-help-section">
                        <strong class="dia-help-subtitle">Tips</strong>
                        <ul class="dia-help-tips">
                            <li>Be specific: "Total incidents in Q1 2024" is better than "Show me incidents"</li>
                            <li>PBIChat only uses your data — it never guesses or makes up numbers</li>
                            <li><strong>Inline mode</strong> takes priority: if columns are in the field well, PBIChat uses them. Remove columns to switch back to database mode</li>
                            <li>Use <strong>Additional Context</strong> in Settings to add business rules (e.g. "Fiscal year starts in April")</li>
                            <li>Each Power BI report can have its own model and database connections</li>
                        </ul>
                    </div>
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

        // Mode toggle buttons
        this.container.querySelectorAll(".dia-mode-btn").forEach((btn) => {
            btn.addEventListener("click", (e) => {
                const mode = (e.target as HTMLElement).getAttribute("data-mode") as "auto" | "inline" | "database";
                this.dataMode = mode;
                this.container.querySelectorAll(".dia-mode-btn").forEach(b => b.classList.remove("active"));
                (e.target as HTMLElement).classList.add("active");
            });
        });

        // Settings button
        this.container.querySelector("#dia-settings-btn")!.addEventListener("click", () => this.openSettings());

        // Clear chat button
        this.container.querySelector("#dia-clear-btn")!.addEventListener("click", () => this.clearChat());

        // Theme toggle
        this.container.querySelector("#dia-theme-btn")!.addEventListener("click", () => this.toggleTheme());
        // Help modal
        this.container.querySelector("#dia-help-btn")!.addEventListener("click", () => {
            this.container.querySelector("#dia-help-overlay")!.classList.add("dia-show");
            this.container.querySelector("#dia-help-modal")!.classList.add("dia-show");
        });
        const closeHelp = () => {
            this.container.querySelector("#dia-help-overlay")!.classList.remove("dia-show");
            this.container.querySelector("#dia-help-modal")!.classList.remove("dia-show");
        };
        this.container.querySelector("#dia-help-close")!.addEventListener("click", closeHelp);
        this.container.querySelector("#dia-help-overlay")!.addEventListener("click", closeHelp);

        // Settings panel
        this.container.querySelector("#dia-settings-close")!.addEventListener("click", () => this.closeSettings());
        this.container.querySelector("#dia-settings-overlay")!.addEventListener("click", () => this.closeSettings());
        (this.container.querySelector("#dia-backend-url-select") as HTMLSelectElement).addEventListener("change", (e) => {
            const val = (e.target as HTMLSelectElement).value;
            const customInput = this.container.querySelector("#dia-backend-url-custom") as HTMLInputElement;
            if (val === "custom") {
                customInput.style.display = "block";
                if (customInput.value) this._backendUrl = customInput.value;
            } else {
                customInput.style.display = "none";
                this._backendUrl = val;
            }
        });
        (this.container.querySelector("#dia-backend-url-custom") as HTMLInputElement).addEventListener("input", (e) => {
            this._backendUrl = (e.target as HTMLInputElement).value;
        });
        this.container.querySelector("#dia-conn-header")!.addEventListener("click", () => {
            const body = this.container.querySelector("#dia-conn-body") as HTMLElement;
            const icon = this.container.querySelector("#dia-conn-collapse-icon") as HTMLElement;
            const collapsed = body.style.display === "none";
            body.style.display = collapsed ? "" : "none";
            icon.textContent = collapsed ? "▾" : "▸";
        });
        this.container.querySelector("#dia-add-conn-btn")!.addEventListener("click", () => this.addConnection());
        this.container.querySelector("#dia-add-tmdl-btn")!.addEventListener("click", () => {
            (this.container.querySelector("#dia-tmdl-file-input") as HTMLInputElement).click();
        });
        (this.container.querySelector("#dia-tmdl-file-input") as HTMLInputElement).addEventListener("change", (e) => this.addTmdlFiles(e));
        this.container.querySelector("#dia-tmdl-clear-btn")!.addEventListener("click", () => this.clearTmdlFiles());
        this.container.querySelector("#dia-tmdl-delete-btn")!.addEventListener("click", () => this.deleteSavedSemanticModel());
        this.container.querySelector("#dia-apply-btn")!.addEventListener("click", () => this.applySettings());

        // LLM Preset event listeners
        (this.container.querySelector("#dia-llm-preset-select") as HTMLSelectElement).addEventListener("change", () => this.selectLLMPreset());
        this.container.querySelector("#dia-save-preset-btn")!.addEventListener("click", () => this.saveLLMPreset());
        this.container.querySelector("#dia-delete-preset-btn")!.addEventListener("click", () => this.deleteLLMPreset());

        // Global keyboard handler -- Escape closes overlays
        this.container.addEventListener("keydown", (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                const settingsPanel = this.container.querySelector("#dia-settings");
                if (settingsPanel?.classList.contains("show")) {
                    this.closeSettings();
                }
            }
        });
    }

    // ======================================
    // SETTINGS
    // ======================================
    private openSettings(): void {
        this.showPasswordPrompt();
    }

    private showPasswordPrompt(): void {
        let overlay = this.container.querySelector("#dia-pw-prompt-overlay") as HTMLElement;
        if (overlay) overlay.remove();

        overlay = document.createElement("div");
        overlay.className = "dia-pw-overlay";
        overlay.id = "dia-pw-prompt-overlay";
        overlay.style.display = "flex";
        overlay.innerHTML = `
            <div class="dia-pw-box">
                <h3>Enter Password</h3>
                <p style="font-size:12px;color:var(--tx2);margin-bottom:12px;">Enter the password to access settings.</p>
                <div class="dia-field">
                    <input type="password" id="dia-pw-prompt-input" placeholder="Password"/>
                </div>
                <div class="dia-pw-error" id="dia-pw-prompt-error"></div>
                <div style="display:flex;gap:8px;">
                    <button class="dia-apply-btn dia-pw-submit" id="dia-pw-prompt-ok" style="flex:1;">Unlock</button>
                    <button class="dia-test-btn" id="dia-pw-prompt-cancel" style="flex:0 0 auto;">Cancel</button>
                </div>
            </div>`;
        this.container.querySelector(".dia-root")!.appendChild(overlay);

        const pwInput = overlay.querySelector("#dia-pw-prompt-input") as HTMLInputElement;
        const errEl = overlay.querySelector("#dia-pw-prompt-error") as HTMLElement;

        const verify = () => {
            const pw = pwInput.value;
            if (!pw) { errEl.textContent = "Please enter the password."; return; }
            if (pw === "Safari99") {
                overlay.remove();
                this.showSettingsPanel();
            } else {
                errEl.textContent = "Incorrect password.";
            }
        };

        overlay.querySelector("#dia-pw-prompt-ok")!.addEventListener("click", verify);
        pwInput.addEventListener("keydown", (e: KeyboardEvent) => { if (e.key === "Enter") verify(); });
        overlay.querySelector("#dia-pw-prompt-cancel")!.addEventListener("click", () => overlay.remove());
        setTimeout(() => pwInput.focus(), 100);
    }

    private showSettingsPanel(): void {
        this.container.querySelector("#dia-settings")!.classList.add("show");
        this.container.querySelector("#dia-settings-overlay")!.classList.add("show");

        // Sync backend URL dropdown with current value
        const urlSelect = this.container.querySelector("#dia-backend-url-select") as HTMLSelectElement;
        const customInput = this.container.querySelector("#dia-backend-url-custom") as HTMLInputElement;
        if (urlSelect) {
            // Check if current URL matches a preset option
            const isPreset = Array.from(urlSelect.options).some(o => o.value !== "custom" && o.value === this._backendUrl);
            if (isPreset) {
                urlSelect.value = this._backendUrl;
                if (customInput) customInput.style.display = "none";
            } else {
                urlSelect.value = "custom";
                if (customInput) { customInput.style.display = "block"; customInput.value = this._backendUrl; }
            }
        }

        this.updateApplyButton();

        // Fetch current config from backend
        if (this.backendUrl) {
            this.loadConfigFromBackend();
        }
    }

    private async loadConfigFromBackend(): Promise<void> {
        try {
            const resp = await fetch(`${this.backendUrl}/config`);
            if (!resp.ok) return;
            const cfg = await resp.json();

            const setVal = (id: string, val: string) => {
                const el = this.container.querySelector(id) as HTMLInputElement | HTMLTextAreaElement;
                if (el) el.value = val || "";
            };

            setVal("#dia-s-extra", cfg.extra_context);

            // Load LLM presets - this will populate the form fields
            this.loadLLMPresets(cfg.llm_presets || {}, cfg.current_llm_preset || "default", cfg);

            // Sync TMDL load state from the backend (single semantic model file).
            this.tmdlLoaded = !!cfg.semantic_model_loaded;
            this.tmdlSizeKb = cfg.semantic_model_chars ? cfg.semantic_model_chars / 1024 : 0;
            this.renderTmdlStatus();

            this.extraContext = cfg.extra_context || "";

            // Load connections only on first load (avoid overwriting user-entered secrets with redacted values)
            if (this.connections.length === 0) {
                try {
                    const connResp = await fetch(`${this.backendUrl}/connections`);
                    if (connResp.ok) {
                        const connData = await connResp.json();
                        this.connections = (connData.connections || []).map((c: any) => {
                            // Mark which secrets are saved on the backend (redacted = saved).
                            // Backend masks the last 8 chars of tokens with `********` so the
                            // user can recognize which token is saved; older versions used `...`.
                            if (c.token && (c.token.endsWith("********") || c.token.endsWith("..."))) {
                                c._tokenSaved = true;
                                c._tokenPreview = c.token;
                                c.token = "";
                            }
                            if (c.password === "***") { c._passwordSaved = true; c.password = ""; }
                            return c;
                        });
                    }
                } catch (_) { /* connections endpoint not available */ }
            }

            this.renderConnectionList();
            this.updateApplyButton();
        } catch (e) {
            // Backend unreachable -- fields stay empty
        }
    }

    private loadLLMPresets(presets: any, currentPreset: string, fallbackConfig?: any): void {
        const select = this.container.querySelector("#dia-llm-preset-select") as HTMLSelectElement;
        if (!select) return;

        // Clear existing options except the first one
        select.innerHTML = '<option value="default">Default (Azure OpenAI)</option>';

        // Add custom presets
        for (const [id, preset] of Object.entries(presets)) {
            if (id !== "default") {
                const option = document.createElement("option");
                option.value = id;
                option.textContent = (preset as any).name || id;
                select.appendChild(option);
            }
        }

        // Set current selection
        select.value = currentPreset;

        // Update form fields based on selected preset
        this.updateFormFromPreset(currentPreset, presets, fallbackConfig);

        // Show/hide delete button
        const deleteBtn = this.container.querySelector("#dia-delete-preset-btn") as HTMLButtonElement;
        if (deleteBtn) {
            deleteBtn.style.display = currentPreset === "default" ? "none" : "inline-block";
        }
    }

    private updateFormFromPreset(presetId: string, presets: any, fallbackConfig?: any): void {
        const preset = presets[presetId];
        
        const setVal = (id: string, val: string) => {
            const el = this.container.querySelector(id) as HTMLInputElement;
            if (el) el.value = val || "";
        };

        if (preset) {
            // Use preset values
            setVal("#dia-api-endpoint", preset.api_endpoint || "");
            setVal("#dia-api-key", ""); // Don't populate API key for security
            setVal("#dia-llm-model", preset.llm_model || "");
        } else if (fallbackConfig) {
            // Use fallback individual config values
            setVal("#dia-api-endpoint", fallbackConfig.api_endpoint || "");
            setVal("#dia-api-key", ""); // Don't populate API key for security
            setVal("#dia-llm-model", fallbackConfig.llm_model || "");
        }
    }

    private async saveLLMPreset(): Promise<void> {
        const presetName = (this.container.querySelector("#dia-preset-name") as HTMLInputElement).value.trim();
        if (!presetName) {
            alert("Please enter a preset name");
            return;
        }

        const apiEndpoint = (this.container.querySelector("#dia-api-endpoint") as HTMLInputElement).value.trim();
        const apiKey = (this.container.querySelector("#dia-api-key") as HTMLInputElement).value.trim();
        const llmModel = (this.container.querySelector("#dia-llm-model") as HTMLInputElement).value.trim();

        // Generate a unique ID
        const presetId = `preset_${Date.now()}`;

        try {
            const resp = await fetch(`${this.backendUrl}/llm-presets`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    preset_id: presetId,
                    preset: {
                        name: presetName,
                        api_endpoint: apiEndpoint,
                        api_key: apiKey,
                        llm_model: llmModel
                    }
                }),
            });

            if (resp.ok) {
                // Automatically select the newly created preset
                await this.selectPresetById(presetId);
                // Clear the preset name field
                (this.container.querySelector("#dia-preset-name") as HTMLInputElement).value = "";
                alert(`Preset "${presetName}" saved successfully!`);
            } else {
                alert("Failed to save preset");
            }
        } catch (e) {
            console.error("Error saving preset:", e);
            alert("Error saving preset");
        }
    }

    private async deleteLLMPreset(): Promise<void> {
        const select = this.container.querySelector("#dia-llm-preset-select") as HTMLSelectElement;
        const presetId = select.value;

        if (presetId === "default") {
            alert("Cannot delete the default preset");
            return;
        }

        if (!confirm("Are you sure you want to delete this preset?")) {
            return;
        }

        try {
            const resp = await fetch(`${this.backendUrl}/llm-presets/${presetId}`, {
                method: "DELETE",
            });

            if (resp.ok) {
                // Reload config to refresh presets
                await this.loadConfigFromBackend();
                alert("Preset deleted successfully!");
            } else {
                alert("Failed to delete preset");
            }
        } catch (e) {
            console.error("Error deleting preset:", e);
            alert("Error deleting preset");
        }
    }

    private async selectPresetById(presetId: string): Promise<void> {
        try {
            const resp = await fetch(`${this.backendUrl}/llm-presets/${presetId}/select`, {
                method: "POST",
            });

            if (resp.ok) {
                // Reload config to refresh UI with the selected preset
                await this.loadConfigFromBackend();
            }
        } catch (e) {
            console.error("Error selecting preset:", e);
        }
    }

    private async selectLLMPreset(): Promise<void> {
        const select = this.container.querySelector("#dia-llm-preset-select") as HTMLSelectElement;
        const presetId = select.value;
        await this.selectPresetById(presetId);
    }

    private closeSettings(): void {
        this.container.querySelector("#dia-settings")!.classList.remove("show");
        this.container.querySelector("#dia-settings-overlay")!.classList.remove("show");
    }

    // -- Theme toggle --

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

    // -- Connection CRUD --

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
                    <label>Access Token${conn._tokenSaved ? ' <span class="dia-saved-badge">✓ Saved</span>' : ''}</label>
                    <input type="password" class="dia-conn-f" data-idx="${idx}" data-field="token" value="${this.escapeHtml(conn.token || "")}" placeholder="${conn._tokenSaved ? "Leave empty to keep saved token" : "dapi..."}"/>
                    ${conn._tokenPreview ? `<div class="dia-saved-preview">Saved: <code>${this.escapeHtml(conn._tokenPreview)}</code></div>` : ""}
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
                    <label>Password${conn._passwordSaved ? ' <span class="dia-saved-badge">✓ Saved</span>' : ''}</label>
                    <input type="password" class="dia-conn-f" data-idx="${idx}" data-field="password" value="${this.escapeHtml(conn.password || "")}" placeholder="${conn._passwordSaved ? "••• saved (leave empty to keep)" : "..."}"/>
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
                const val = el.value.trim();
                // For secret fields: only overwrite if user actually typed a new value
                if (field === "token") {
                    if (val) {
                        // User entered a new token — use it and clear the saved flag
                        (this.connections[i] as any).token = val;
                        (this.connections[i] as any)._tokenSaved = false;
                    }
                    // If empty, leave token and _tokenSaved as-is (backend will use __KEEP__)
                } else if (field === "password") {
                    if (val) {
                        (this.connections[i] as any).password = val;
                        (this.connections[i] as any)._passwordSaved = false;
                    }
                } else {
                    (this.connections[i] as any)[field] = val;
                }
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
        resultEl.textContent = `Testing ${conn.name || "connection"}...`;

        const url = this.getSettingsUrl();
        if (!url) {
            resultEl.className = "dia-test-result dia-conn-result fail";
            resultEl.textContent = "Backend not available.";
            return;
        }

        try {
            // Save connections first so the backend knows about this connection
            await fetch(`${url}/connections`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ connections: this.connections }),
            });

            const resp = await fetch(`${url}/test-connection/${conn.id}`, { method: "POST" });
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

        // Filter for .tmdl files from the selected folder (including subfolders)
        this.pendingTmdlFiles = [];
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            if (!file.name.toLowerCase().endsWith(".tmdl")) continue;
            const text = await file.text();
            // Use the relative path to preserve subfolder structure and avoid dedup collisions
            const name = (file as any).webkitRelativePath || file.name;
            // Dedup by name -- last occurrence wins
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
        const count = this.pendingTmdlFiles.length;

        if (count === 0) {
            listEl.style.display = "none";
            clearBtn.style.display = "none";
            this.updateApplyButton();
            return;
        }

        clearBtn.style.display = "";
        listEl.style.display = "block";
        this.updateApplyButton();

        const names = this.pendingTmdlFiles.map(f => f.name).sort();
        listEl.innerHTML =
            `<div class="dia-tmdl-count">${count} .tmdl file${count !== 1 ? "s" : ""} ready to upload</div>` +
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
            const payload: any = { files: this.pendingTmdlFiles };
            // Save current connections alongside the model
            if (this.connections.length > 0) {
                payload.connections = this.connections;
            }

            const resp = await fetch(`${url}/upload-tmdl`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `Error ${resp.status}`);
            }

            const data = await resp.json();
            resultEl.className = "dia-test-result ok";
            const skipped = data.files_skipped ? ` (${data.files_skipped} auto-generated skipped)` : "";
            const sizeKb = (data.total_chars / 1024);
            resultEl.textContent = `Uploaded ${data.files_loaded} .tmdl file(s) (${sizeKb.toFixed(1)} KB)${skipped}`;
            this.pendingTmdlFiles = [];
            this.renderTmdlFileList();
            this.tmdlLoaded = true;
            this.tmdlSizeKb = sizeKb;
            this.renderTmdlStatus();
            this.updateApplyButton();
        } catch (err: any) {
            resultEl.className = "dia-test-result fail";
            resultEl.textContent = err.message || "Failed to upload TMDL files.";
            // Keep files staged so user can retry
        }
    }

    private renderTmdlStatus(): void {
        const statusEl = this.container.querySelector("#dia-tmdl-status") as HTMLElement;
        const deleteBtn = this.container.querySelector("#dia-tmdl-delete-btn") as HTMLElement;
        if (!statusEl) return;
        if (this.tmdlLoaded) {
            statusEl.innerHTML = `<span class="dia-saved-badge">✓ Saved</span> Semantic model loaded (${this.tmdlSizeKb.toFixed(1)} KB)`;
            statusEl.className = "dia-tmdl-status loaded";
            if (deleteBtn) deleteBtn.style.display = "";
        } else {
            statusEl.textContent = "No semantic model uploaded yet.";
            statusEl.className = "dia-tmdl-status";
            if (deleteBtn) deleteBtn.style.display = "none";
        }
    }

    private async deleteSavedSemanticModel(): Promise<void> {
        const confirmed = await this.showConfirm(
            "Delete the saved semantic model? You'll need to upload a new TMDL folder to chat with database context again."
        );
        if (!confirmed) return;

        const resultEl = this.container.querySelector("#dia-tmdl-result") as HTMLElement;
        try {
            const resp = await fetch(`${this.backendUrl}/semantic-model`, { method: "DELETE" });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `Error ${resp.status}`);
            }
            this.tmdlLoaded = false;
            this.tmdlSizeKb = 0;
            this.renderTmdlStatus();
            if (resultEl) { resultEl.style.display = "none"; }
            this.updateApplyButton();
        } catch (err: any) {
            if (resultEl) {
                resultEl.className = "dia-test-result fail";
                resultEl.style.display = "block";
                resultEl.textContent = err.message || "Failed to delete semantic model.";
            }
        }
    }

    private updateApplyButton(): void {
        const btn = this.container.querySelector("#dia-apply-btn") as HTMLButtonElement;
        const warn = this.container.querySelector("#dia-tmdl-warning") as HTMLElement;
        // Always enable Apply -- TMDL upload happens automatically on Apply
        if (btn) {
            btn.disabled = false;
        }
        if (warn) {
            warn.style.display = (this.tmdlLoaded || this.pendingTmdlFiles.length > 0) ? "none" : "block";
        }
    }

    private getSettingsUrl(): string {
        return this.backendUrl;
    }

    private async applySettings(): Promise<void> {
        const applyBtn = this.container.querySelector("#dia-apply-btn") as HTMLButtonElement;
        const prevText = applyBtn.textContent;
        applyBtn.textContent = "Saving...";
        applyBtn.disabled = true;

        const extra = (this.container.querySelector("#dia-s-extra") as HTMLTextAreaElement).value.trim();
        this.extraContext = extra;

        const apiEndpoint = (this.container.querySelector("#dia-api-endpoint") as HTMLInputElement).value.trim();
        const apiKey = (this.container.querySelector("#dia-api-key") as HTMLInputElement).value.trim();
        const llmModel = (this.container.querySelector("#dia-llm-model") as HTMLInputElement).value.trim();

        // Upload pending TMDL files if any
        if (this.pendingTmdlFiles.length > 0) {
            applyBtn.textContent = "Uploading TMDL files...";
            await this.uploadTmdlFiles();
        }

        // TMDL is required before saving connections or LLM config
        if (!this.tmdlLoaded) {
            applyBtn.textContent = prevText;
            applyBtn.disabled = false;
            this.showError("Please upload your semantic model (.tmdl files) first. The TMDL is required before configuring connections or AI settings.");
            return;
        }

        // Save configuration via /config
        try {
            await fetch(`${this.backendUrl}/config`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    extra_context: extra,
                    api_endpoint: apiEndpoint || null,
                    api_key: apiKey || null,
                    llm_model: llmModel || null
                }),
            });
        } catch (_) { /* settings still applied locally */ }

        // Save connections via /connections
        this.readConnectionsFromUI();
        try {
            // For empty secret fields that were previously saved, send sentinel so backend preserves originals
            const connsToSend = this.connections.map((c: any) => {
                const copy = { ...c };
                if (!copy.token && copy._tokenSaved) copy.token = "__KEEP__";
                if (!copy.password && copy._passwordSaved) copy.password = "__KEEP__";
                delete copy._tokenSaved;
                delete copy._passwordSaved;
                return copy;
            });
            await fetch(`${this.backendUrl}/connections`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ connections: connsToSend }),
            });
        } catch (_) { /* silently fail */ }

        applyBtn.textContent = prevText;
        applyBtn.disabled = false;
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
                    // Has connections -- test the first Databricks one (wakes warehouse)
                    const resp = await fetch(`${this.backendUrl}/test-connection`, {
                        method: "POST",
                    });
                    const data = await resp.json();

                    if (data.status === "connected") {
                        this.warehouseState = "RUNNING";
                        this.renderStatusDot(statusEl);
                        if (dot) dot.className = "dia-dot dia-dot-on";
                        this.stopPolling();
                        return;
                    }

                    if (data.status === "starting" || data.state === "STOPPED" || data.state === "STARTING") {
                        this.warehouseState = data.state || "STARTING";
                        statusEl.innerHTML = `<span class="dia-dot dia-dot-starting"></span> Warehouse starting — waiting...`;
                        if (dot) dot.className = "dia-dot dia-dot-starting";
                        this.startWakePolling();
                        return;
                    }

                    // If test-connection returned an error but connections exist,
                    // they might all be SQL Server (no Databricks warehouse to wake)
                    if (data.detail && data.detail.includes("No Databricks")) {
                        // Only SQL Server connections -- show connected
                        this.warehouseState = "RUNNING";
                        this.renderStatusDot(statusEl);
                        if (dot) dot.className = "dia-dot dia-dot-on";
                        this.stopPolling();
                        return;
                    }

                    this.warehouseState = "ERROR";
                    statusEl.innerHTML = `<span class="dia-dot dia-dot-error"></span> ${data.message || data.detail || "Connection failed"}`;
                    if (dot) dot.className = "dia-dot dia-dot-error";
                    return;
                }
            }

            // No connections configured
            this.warehouseState = "RUNNING";
            statusEl.innerHTML = `<span class="dia-dot dia-dot-on"></span> Backend connected`;
            if (dot) dot.className = "dia-dot dia-dot-on";
        } catch (e: any) {
            this.warehouseState = "ERROR";
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

                // Still starting -- update elapsed time
                if (statusEl) {
                    statusEl.innerHTML = `<span class="dia-dot dia-dot-starting"></span> Warehouse starting... (${polls * 5}s)`;
                }
            } catch (e) {
                // Transient error -- keep polling
            }
        }, 5000) as unknown as number;
    }

    // ======================================
    // POWER BI UPDATE (reads settings)
    // ======================================
    public update(options: VisualUpdateOptions): void {
        this.host.eventService.renderingStarted(options);

        if (options && options.dataViews && options.dataViews[0]) {
            const dv = options.dataViews[0];
            this.fmtSettings = this.fmtService.populateFormattingSettingsModel(PBIChatFormattingSettings, dv);
        }

        this.loadPersistedData(options);

        // Extract inline data from field wells
        this.inlineDataCsv = "";
        this.inlineStats = "";
        this.inlineColumnCount = 0;
        this.inlineRowCount = 0;
        this.inlineRowsSent = 0;
        this.inlineTruncated = false;

        if (options && options.dataViews && options.dataViews[0]) {
            const table = options.dataViews[0].table;
            if (table && table.columns && table.columns.length > 0 && table.rows && table.rows.length > 0) {
                this.inlineColumnCount = table.columns.length;
                this.inlineRowCount = table.rows.length;

                // Compute summary stats from ALL rows
                const stats: Record<string, Record<string, unknown>> = {};
                for (let ci = 0; ci < table.columns.length; ci++) {
                    const col = table.columns[ci];
                    const name = col.displayName;
                    let count = 0, nulls = 0, sum = 0, min = Infinity, max = -Infinity;
                    let isNumeric = true;
                    const distinct = new Set<string>();

                    for (let ri = 0; ri < table.rows.length; ri++) {
                        const val = table.rows[ri][ci];
                        if (val == null || val === "") { nulls++; continue; }
                        count++;
                        const s = String(val);
                        distinct.add(s);
                        const num = Number(val);
                        if (!isNaN(num) && isNumeric) {
                            sum += num;
                            if (num < min) min = num;
                            if (num > max) max = num;
                        } else {
                            isNumeric = false;
                        }
                    }

                    if (isNumeric && count > 0) {
                        stats[name] = {
                            type: "numeric", count, nulls,
                            sum: Math.round(sum * 100) / 100,
                            avg: Math.round((sum / count) * 100) / 100,
                            min: min === Infinity ? null : min,
                            max: max === -Infinity ? null : max,
                            distinct: distinct.size,
                        };
                    } else {
                        stats[name] = {
                            type: "text", count, nulls,
                            distinct: distinct.size,
                            top5: [...distinct].slice(0, 5),
                        };
                    }
                }
                this.inlineStats = JSON.stringify({
                    total_rows: table.rows.length,
                    columns: stats,
                });

                // Serialize rows to CSV (char-limited)
                const MAX_CHARS = 400000;
                const header = table.columns.map(c => this.csvEscape(c.displayName)).join(",");
                let csv = header + "\n";
                let rowsSent = 0;

                for (let i = 0; i < table.rows.length; i++) {
                    const line = table.columns.map((_, ci) => {
                        const val = table.rows[i][ci];
                        return val == null ? "" : this.csvEscape(String(val));
                    }).join(",");
                    if (csv.length + line.length + 1 > MAX_CHARS) {
                        this.inlineTruncated = true;
                        break;
                    }
                    csv += line + "\n";
                    rowsSent++;
                }
                if (rowsSent < table.rows.length) this.inlineTruncated = true;
                this.inlineRowsSent = rowsSent;
                this.inlineDataCsv = csv;
            }
        }

        // Sync TMDL + connection state from the backend on every update so the
        // visual reflects the canonical server state (e.g. after Power BI
        // destroys and recreates the visual on a page switch).
        if (this.backendUrl) {
            this.refreshBackendState();
        }

        // High contrast mode support
        this.applyHighContrast();

        this.updateStatus();
        this.host.eventService.renderingFinished(options);
    }

    private async refreshBackendState(): Promise<void> {
        try {
            const resp = await fetch(`${this.backendUrl}/config`);
            if (!resp.ok) return;
            const cfg = await resp.json();
            this.tmdlLoaded = !!cfg.semantic_model_loaded;
            this.tmdlSizeKb = cfg.semantic_model_chars ? cfg.semantic_model_chars / 1024 : 0;
            this.renderTmdlStatus();

            // Pull connections only when our in-memory list is empty (avoid
            // overwriting user-entered secrets with redacted values).
            if (this.connections.length === 0) {
                const connResp = await fetch(`${this.backendUrl}/connections`);
                if (connResp.ok) {
                    const connData = await connResp.json();
                    this.connections = (connData.connections || []).map((c: any) => {
                        if (c.token && (c.token.endsWith("********") || c.token.endsWith("..."))) {
                            c._tokenSaved = true;
                            c._tokenPreview = c.token;
                            c.token = "";
                        }
                        if (c.password === "***") { c._passwordSaved = true; c.password = ""; }
                        return c;
                    });
                }
            }
        } catch (_) {
            // Backend not reachable — state will remain unloaded
        }
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

    // ======================================
    // CLEAR CHAT
    // ======================================
    private clearChat(): void {
        this.history = [];

        try {
            localStorage.removeItem("pbichat_history");
        } catch (e) {
            console.warn("Failed to clear chat history from localStorage:", e);
        }

        try {
            sessionStorage.removeItem("pbichat_history");
        } catch (e) {
            console.warn("Failed to clear chat history from sessionStorage:", e);
        }
        
        // Clear from Power BI persisted data
        this.persistData();
        
        this.msgsEl.innerHTML = "";
        const welcome = document.createElement("div");
        welcome.className = "dia-welcome";
        welcome.id = "dia-welcome";
        const connected = this.backendUrl && this.warehouseState && this.warehouseState !== "ERROR";
        welcome.innerHTML = `
            <p class="dia-welcome-sub">AI-powered data assistant with live SQL access</p>
            <div class="dia-setup-banner" id="dia-setup-banner" style="${connected ? "display:none" : ""}">
                <div class="dia-setup-title">Get Started</div>
                <div class="dia-setup-steps">
                    <div class="dia-setup-step"><span class="dia-step-num">1</span> Click <strong>Settings</strong> and enter the password</div>
                    <div class="dia-setup-step"><span class="dia-step-num">2</span> Add a <strong>database connection</strong> (Databricks or SQL Server)</div>
                    <div class="dia-setup-step"><span class="dia-step-num">3</span> Select your <strong>semantic model folder</strong> (.tmdl files)</div>
                    <div class="dia-setup-step"><span class="dia-step-num">4</span> Click <strong>Apply &amp; Close</strong> to save</div>
                </div>
            </div>
            <div class="dia-sugs">
                <button class="dia-sug" data-q="What tables are in my database?">What tables are in my database?</button>
                <button class="dia-sug" data-q="Show me a summary of the data">Show me a summary of the data</button>
                <button class="dia-sug" data-q="Write a DAX measure for this metric">Write a DAX measure for this metric</button>
                <button class="dia-sug" data-q="Show me the latest incidents for NS&amp;E projects for the last 7 days">Show me the latest incidents for NS&amp;E projects for the last 7 days</button>
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

    // ======================================
    // CHAT LOGIC
    // ======================================
    private async send(): Promise<void> {
        const text = this.inputEl.value.trim();
        if (!text || this.busy) return;

        if (!this.backendUrl) {
            this.showError("Backend API URL not set. Click Settings to configure.");
            return;
        }

        // Determine effective mode
        const useInline = this.dataMode === "inline" || (this.dataMode === "auto" && !!this.inlineDataCsv);
        const useDatabase = this.dataMode === "database" || (this.dataMode === "auto" && !this.inlineDataCsv);

        // TMDL is required for all AI features. Re-sync from the backend
        // before failing — Power BI page switches destroy and recreate the
        // visual, and the async refresh in update() may not have landed by
        // the time the user presses Send. This avoids a spurious error when
        // the semantic model actually is loaded on the server.
        if (!this.tmdlLoaded) {
            await this.refreshBackendState();
        }
        if (!this.tmdlLoaded) {
            this.showError("Semantic model (.tmdl files) must be loaded before using AI features. Open Settings to upload your .tmdl files.");
            return;
        }

        // Validate data source is available for chosen mode
        if (useInline && !this.inlineDataCsv) {
            this.showError("Inline Data mode selected but no columns are in the field well. Drag columns into the Columns field first.");
            return;
        }

        this.busy = true;
        this.sendBtn.disabled = true;
        this.inputEl.value = "";
        this.inputEl.style.height = "auto";

        this.addMessage("user", text);
        this.showTyping();

        try {
            // Skip warehouse check in inline data mode
            if (!useInline) {
                // Pre-check warehouse state -- show status banner while waiting
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
            }

            const payload: Record<string, unknown> = {
                message: text,
                history: this.history.slice(-20),
                extra_context: this.extraContext || null,
            };
            if (useInline) {
                payload.inline_data = this.inlineDataCsv;
                payload.inline_stats = this.inlineStats;
            }

            const resp = await fetch(`${this.backendUrl}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            this.hideTyping();

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `API error ${resp.status}`);
            }

            const data = await resp.json();

            this.history.push({ role: "user", content: text });
            this.history.push({ role: "assistant", content: data.response });
            this.addMessage("ai", data.response);
            this.persistData();

        } catch (e: any) {
            this.hideTyping();
            this.showError(e.message || "Request failed.");
        }

        this.busy = false;
        this.sendBtn.disabled = false;
        this.inputEl.focus();
    }

    private csvEscape(val: string): string {
        if (val.includes(",") || val.includes('"') || val.includes("\n")) {
            return '"' + val.replace(/"/g, '""') + '"';
        }
        return val;
    }

    // ======================================
    // UI HELPERS
    // ======================================
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

    /** Show a custom in-app confirm dialog (browser confirm() is blocked in Power BI iframe). */
    private showConfirm(message: string, okLabel: string = "Delete"): Promise<boolean> {
        return new Promise((resolve) => {
            const overlay = this.container.querySelector("#dia-confirm-overlay") as HTMLElement;
            const dialog = this.container.querySelector("#dia-confirm-dialog") as HTMLElement;
            const msgEl = this.container.querySelector("#dia-confirm-msg") as HTMLElement;
            const okBtn = this.container.querySelector("#dia-confirm-ok") as HTMLButtonElement;
            const cancelBtn = this.container.querySelector("#dia-confirm-cancel") as HTMLButtonElement;

            msgEl.textContent = message;
            okBtn.textContent = okLabel;
            overlay.style.display = "block";
            dialog.style.display = "block";

            const cleanup = () => {
                overlay.style.display = "none";
                dialog.style.display = "none";
                okBtn.removeEventListener("click", onOk);
                cancelBtn.removeEventListener("click", onCancel);
                overlay.removeEventListener("click", onCancel);
            };
            const onOk = () => { cleanup(); resolve(true); };
            const onCancel = () => { cleanup(); resolve(false); };

            okBtn.addEventListener("click", onOk);
            cancelBtn.addEventListener("click", onCancel);
            overlay.addEventListener("click", onCancel);
        });
    }

    private showError(rawMsg: string): void {
        // Map common technical errors to user-friendly messages
        let msg = rawMsg;
        if (/fetch|network|ERR_CONNECTION|ECONNREFUSED/i.test(msg)) {
            msg = "Cannot reach the backend server. Please check your Backend API URL in Settings.";
        } else if (/403|Invalid password/i.test(msg)) {
            msg = "Authentication failed. Please re-enter the password in Settings.";
        } else if (/429|rate limit/i.test(msg)) {
            msg = "You're sending messages too quickly. Please wait a moment and try again.";
        } else if (/500|Internal Server/i.test(msg)) {
            msg = "The server encountered an error. Please try again or check the backend logs.";
        } else if (/502|LLM API error/i.test(msg)) {
            msg = "The AI service is temporarily unavailable. Please try again in a moment.";
        } else if (/not configured/i.test(msg)) {
            msg = "The AI API key is not configured. Please contact your administrator.";
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
            `<div class="dia-wh-sub">This usually takes 2-5 minutes. Your query will run automatically once ready.</div>`,
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

    // ======================================
    // WAREHOUSE STATUS
    // ======================================
    private updateStatus(): void {
        const el = this.container.querySelector("#dia-status") as HTMLElement;
        if (!el) return;

        // Update connection dot in settings button
        const dot = this.container.querySelector("#dia-conn-dot") as HTMLElement;

        // Show/hide setup banner based on connection state
        const banner = this.container.querySelector("#dia-setup-banner") as HTMLElement;

        // Inline data mode: show data badge
        if (this.inlineDataCsv) {
            const rowLabel = this.inlineTruncated
                ? `${this.inlineRowsSent.toLocaleString()} of ${this.inlineRowCount.toLocaleString()} rows`
                : `${this.inlineRowCount.toLocaleString()} rows`;
            el.innerHTML = `<span class="dia-dot dia-dot-on"></span> ${this.inlineColumnCount} cols \u00d7 ${rowLabel}`;
            if (dot) dot.className = "dia-dot dia-dot-on";
            if (banner) banner.style.display = "none";
            this.stopPolling();
            return;
        }

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

        // Refresh warehouse status periodically
        if (!this.warehouseState) {
            this.checkWarehouseStatus();
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
            const resp = await fetch(`${this.backendUrl}/warehouse-status`);
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
                const resp = await fetch(`${this.backendUrl}/warehouse-status`);
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

    // ======================================
    // CHART RENDERING
    // ======================================
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
                // Fall back to bar chart if type is unrecognized
                if (!this.ALL_CHARTS.includes(spec.type)) {
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
                        // Bar charts -- gradient fill
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

        // Markdown tables: detect consecutive lines starting with |
        t = t.replace(/((?:^\|.+\|$\n?){2,})/gm, (_match, tableBlock: string) => {
            const rows = tableBlock.trim().split("\n").filter((r: string) => r.trim());
            if (rows.length < 2) return tableBlock;
            let html = '<div class="dia-table-wrap"><table class="dia-table">';
            for (let ri = 0; ri < rows.length; ri++) {
                const row = rows[ri].trim();
                // Skip separator row (|---|---|)
                if (/^\|[\s\-:]+\|$/.test(row.replace(/\|/g, (m: string, offset: number, str: string) => {
                    // Check if row is only pipes, dashes, colons, spaces
                    return m;
                })) && /^[\|\s\-:]+$/.test(row)) continue;
                const cells = row.split("|").slice(1, -1); // remove leading/trailing empty from split
                const tag = ri === 0 ? "th" : "td";
                html += "<tr>";
                for (const cell of cells) {
                    html += `<${tag}>${cell.trim()}</${tag}>`;
                }
                html += "</tr>";
            }
            html += "</table></div>";
            return html;
        });

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
                        // Consecutive chart -- keep accumulating
                        pending.push(chartIdx);
                    } else {
                        // Non-empty text breaks the run -- flush pending
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
            // Single chart -- just inline it
            t = t.replace(CHART_PLACEHOLDER, chartHtmls[0]);
        }

        return t;
    }

    private loadPersistedData(options: VisualUpdateOptions): void {
        // PRIORITY 1: Restore from Power BI metadata (survives across page switches)
        if (options && options.dataViews && options.dataViews[0] && options.dataViews[0].metadata.objects) {
            const obj = (options.dataViews[0].metadata.objects as any).pbichat;
            if (obj && typeof obj.historyJson === "string" && obj.historyJson.length > 0) {
                try {
                    this.history = JSON.parse(obj.historyJson);
                    this.renderMessages();
                    return;
                } catch (e) {
                    console.warn("Failed to parse persisted chat history:", e);
                }
            }
        }

        // PRIORITY 2: sessionStorage (within-session backup)
        try {
            const sessionStored = sessionStorage.getItem("pbichat_history");
            if (sessionStored) {
                this.history = JSON.parse(sessionStored);
                if (this.history.length > 0) {
                    this.renderMessages();
                    return;
                }
            }
        } catch (e) {
            console.warn("Failed to load from sessionStorage:", e);
        }

        // PRIORITY 3: localStorage (cross-session fallback)
        try {
            const stored = localStorage.getItem("pbichat_history");
            if (stored) {
                this.history = JSON.parse(stored);
                if (this.history.length > 0) {
                    this.renderMessages();
                    return;
                }
            }
        } catch (e) {
            console.warn("Failed to load from localStorage:", e);
        }
    }

    private renderMessages(): void {
        // Clear existing messages except welcome
        const msgs = this.msgsEl.querySelectorAll(".dia-msg");
        msgs.forEach(msg => msg.remove());
        // Re-add welcome if no messages
        if (this.history.length === 0 && !this.welcomeEl.parentNode) {
            this.msgsEl.appendChild(this.welcomeEl);
        }
        // Add messages
        for (const msg of this.history) {
            // Convert 'assistant' role to 'ai' for addMessage
            const displayRole = msg.role === "assistant" ? "ai" : "user";
            this.addMessage(displayRole, msg.content);
        }
    }

    private persistData(): void {
        const serialized = JSON.stringify(this.history);

        try {
            localStorage.setItem("pbichat_history", serialized);
        } catch (e) {
            console.warn("Failed to save chat history to localStorage:", e);
        }

        try {
            sessionStorage.setItem("pbichat_history", serialized);
        } catch (e) {
            console.warn("Failed to save to sessionStorage:", e);
        }

        try {
            this.host.persistProperties({
                merge: [{
                    objectName: "pbichat",
                    selector: {},
                    properties: { historyJson: serialized }
                } as any]
            });
        } catch (e) {
            console.warn("Failed to persist to Power BI:", e);
        }
    }
}
