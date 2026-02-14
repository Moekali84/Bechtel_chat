"""
PBIChat — Backend API
Orchestrates the agentic loop: User question → LLM → SQL → Database → LLM → Response
Deploy on Azure App Service, Azure Functions, or any Python host.
"""

import os
import re
import time
import asyncio
import secrets
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
import httpx
import jwt
import stripe
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from typing import Optional
from supabase import create_client, Client as SupabaseClient
import json

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

app = FastAPI(title="PBIChat API")

# ══════════════════════════════════════════════════════════
# CORS — restrict to your Power BI domain in production
# ══════════════════════════════════════════════════════════
# In production, replace ["*"] with specific origins:
#   allow_origins=["https://app.powerbi.com", "https://your-company.powerbi.com"]
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════════════════
# RATE LIMITING — per-IP, configurable via env
# ══════════════════════════════════════════════════════════
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))  # requests per minute
_rate_buckets: dict[str, list[float]] = defaultdict(list)

def _check_rate_limit(client_ip: str):
    """Simple sliding-window rate limiter. Raises 429 if exceeded."""
    now = time.time()
    window = _rate_buckets[client_ip]
    # Prune entries older than 60s
    cutoff = now - 60
    _rate_buckets[client_ip] = [t for t in window if t > cutoff]
    if len(_rate_buckets[client_ip]) >= RATE_LIMIT_RPM:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait a moment.")
    _rate_buckets[client_ip].append(now)

# ══════════════════════════════════════════════════════════
# CONFIG — Set via environment variables or .env file
# ══════════════════════════════════════════════════════════
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4")
_raw_db_host = os.getenv("DATABRICKS_HOST", "").strip()
DATABRICKS_HOST = _raw_db_host if not _raw_db_host or _raw_db_host.startswith("http") else f"https://{_raw_db_host}"
DATABRICKS_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH", "")  # /sql/1.0/warehouses/xxx
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
DATABRICKS_CATALOG_SCHEMA = os.getenv("DATABRICKS_CATALOG_SCHEMA", "")  # e.g. silver_poland_nonprod.iris
SEMANTIC_MODEL_FILE = ENV_PATH.parent / "semantic_model.txt"
# Load semantic model from file if it exists, otherwise fall back to .env value
if SEMANTIC_MODEL_FILE.exists():
    SEMANTIC_MODEL = SEMANTIC_MODEL_FILE.read_text()
else:
    SEMANTIC_MODEL = os.getenv("SEMANTIC_MODEL", "")
EXTRA_CONTEXT = os.getenv("EXTRA_CONTEXT", "")
SETTINGS_PASSWORD = os.getenv("SETTINGS_PASSWORD", "")

# ══════════════════════════════════════════════════════════
# AUTHENTICATION — password via X-Auth-Password header
# ══════════════════════════════════════════════════════════
def require_auth(x_auth_password: str = Header(..., alias="X-Auth-Password")):
    """Dependency: validates the settings password sent via header."""
    if not SETTINGS_PASSWORD:
        raise HTTPException(status_code=500, detail="SETTINGS_PASSWORD not configured on the server. Set it in .env.")
    if not secrets.compare_digest(x_auth_password, SETTINGS_PASSWORD):
        raise HTTPException(status_code=403, detail="Invalid password.")


def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[str]:
    """Dependency: decode Supabase JWT and return user_id, or None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    if not SUPABASE_JWT_SECRET:
        return None
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload.get("sub")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def require_user(authorization: Optional[str] = Header(None)) -> str:
    """Dependency: like get_current_user but raises 401 if not authenticated."""
    user_id = get_current_user(authorization)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    return user_id


# ══════════════════════════════════════════════════════════
# LICENSE DATABASE (Supabase)
# ══════════════════════════════════════════════════════════
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

_supabase: Optional[SupabaseClient] = None

def _get_sb() -> SupabaseClient:
    """Get the Supabase client (lazy-initialized)."""
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise HTTPException(status_code=500, detail="SUPABASE_URL and SUPABASE_KEY must be set in .env")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS licenses (
    key             TEXT PRIMARY KEY,
    tier            TEXT NOT NULL DEFAULT 'pro',
    label           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    max_connections INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id              BIGSERIAL PRIMARY KEY,
    license_key     TEXT,
    user_id         TEXT,
    client_ip       TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usage_day
    ON usage_log (license_key, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_user
    ON usage_log (user_id, created_at);
"""

_sb_tables_ready = False

_sb_users_ready = False

def _init_db():
    """Check if license tables exist in Supabase. Print setup SQL if missing."""
    global _sb_tables_ready, _sb_users_ready
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("PBICHAT: SUPABASE_URL/SUPABASE_KEY not set — licensing disabled.")
        return
    try:
        sb = _get_sb()
        sb.table("licenses").select("key", count="exact").limit(0).execute()
        sb.table("usage_log").select("id", count="exact").limit(0).execute()
        _sb_tables_ready = True
        print("PBICHAT: Supabase license tables OK.")
    except Exception as e:
        err = str(e)
        if "could not find" in err.lower() or "does not exist" in err.lower() or "PGRST" in err:
            print("\n" + "=" * 60)
            print("PBICHAT: License tables not found in Supabase.")
            print("Run this SQL in your Supabase SQL Editor")
            print(f"  ({SUPABASE_URL.replace('.co', '.co/project/')}/sql/new):")
            print("=" * 60)
            print(_INIT_SQL)
            print("=" * 60 + "\n")
        else:
            print(f"PBICHAT: Supabase connection error: {err[:200]}")

    # Probe user management tables (graceful if missing)
    try:
        sb = _get_sb()
        sb.table("users").select("id", count="exact").limit(0).execute()
        sb.table("subscriptions").select("id", count="exact").limit(0).execute()
        sb.table("payments").select("id", count="exact").limit(0).execute()
        _sb_users_ready = True
        print("PBICHAT: Supabase user/billing tables OK.")
    except Exception:
        print("PBICHAT: User/billing tables not found — auth/billing endpoints disabled until tables are created.")

_init_db()

# ══════════════════════════════════════════════════════════
# LICENSING — tier definitions and validation
# ══════════════════════════════════════════════════════════
FREE_DAILY_QUERY_LIMIT = int(os.getenv("FREE_DAILY_QUERY_LIMIT", "5"))
FREE_MAX_CONNECTIONS = 1
FREE_CHART_TYPES = {"bar", "line", "pie"}
ALL_CHART_TYPES = {"bar", "line", "pie", "doughnut", "scatter", "horizontalBar"}

@dataclass
class LicenseInfo:
    """Resolved license state for the current request."""
    tier: str                           # "free" or "pro"
    key: Optional[str]                  # None for free tier
    label: str                          # "" for free
    daily_used: int                     # queries used today
    daily_limit: Optional[int]          # None = unlimited
    max_connections: Optional[int]      # None = unlimited
    allowed_charts: set = field(default_factory=lambda: FREE_CHART_TYPES)

def resolve_license(license_key: Optional[str], client_ip: str, user_id: Optional[str] = None) -> LicenseInfo:
    """Look up a license key and return its tier + usage.
    Checks users.license_key first (new system), then falls back to licenses table."""
    if not _sb_tables_ready:
        # Supabase not configured or tables missing — default to unlimited (no enforcement)
        return LicenseInfo(
            tier="pro", key=None, label="(no license DB)",
            daily_used=0, daily_limit=None, max_connections=None,
            allowed_charts=ALL_CHART_TYPES,
        )

    sb = _get_sb()
    today = date.today().isoformat()

    # ── New system: check users table first ──
    if license_key and _sb_users_ready:
        try:
            resp = sb.table("users").select("id, email, tier, license_key").eq("license_key", license_key).execute()
            if resp.data:
                user = resp.data[0]
                uid = user["id"]
                tier = user["tier"]
                if tier == "pro":
                    return LicenseInfo(
                        tier="pro", key=license_key, label=user["email"],
                        daily_used=0, daily_limit=None, max_connections=None,
                        allowed_charts=ALL_CHART_TYPES,
                    )
                # Free tier with account — count usage by user_id
                usage_resp = sb.table("usage_log") \
                    .select("id", count="exact") \
                    .eq("user_id", uid) \
                    .gte("created_at", today) \
                    .execute()
                used = usage_resp.count if usage_resp.count is not None else 0
                return LicenseInfo(
                    tier="free", key=license_key, label=user["email"],
                    daily_used=used,
                    daily_limit=FREE_DAILY_QUERY_LIMIT,
                    max_connections=FREE_MAX_CONNECTIONS,
                    allowed_charts=FREE_CHART_TYPES,
                )
        except Exception:
            pass  # Fall through to legacy lookup

    if not license_key:
        # Free tier — count today's usage by user_id (if logged in) or IP
        if user_id and _sb_users_ready:
            resp = sb.table("usage_log") \
                .select("id", count="exact") \
                .eq("user_id", user_id) \
                .gte("created_at", today) \
                .execute()
        else:
            resp = sb.table("usage_log") \
                .select("id", count="exact") \
                .is_("license_key", "null") \
                .is_("user_id", "null") \
                .eq("client_ip", client_ip) \
                .gte("created_at", today) \
                .execute()
        used = resp.count if resp.count is not None else 0

        return LicenseInfo(
            tier="free", key=None, label="",
            daily_used=used,
            daily_limit=FREE_DAILY_QUERY_LIMIT,
            max_connections=FREE_MAX_CONNECTIONS,
            allowed_charts=FREE_CHART_TYPES,
        )

    # ── Legacy: Validate key in licenses table ──
    resp = sb.table("licenses").select("*").eq("key", license_key).execute()
    if not resp.data:
        raise HTTPException(status_code=403, detail="Invalid license key.")

    lic = resp.data[0]

    if not lic["is_active"]:
        raise HTTPException(status_code=403, detail="License key has been revoked.")

    if lic.get("expires_at"):
        exp = datetime.fromisoformat(lic["expires_at"])
        if datetime.now(timezone.utc) > exp:
            raise HTTPException(status_code=403, detail="License key has expired.")

    return LicenseInfo(
        tier=lic["tier"],
        key=license_key,
        label=lic["label"],
        daily_used=0,
        daily_limit=None,
        max_connections=lic.get("max_connections"),
        allowed_charts=ALL_CHART_TYPES,
    )

def record_usage(license_key: Optional[str], client_ip: str, endpoint: str, user_id: Optional[str] = None):
    """Record a usage event in Supabase."""
    if not _sb_tables_ready:
        return
    sb = _get_sb()
    row = {
        "license_key": license_key,
        "client_ip": client_ip,
        "endpoint": endpoint,
    }
    if user_id:
        row["user_id"] = user_id
    sb.table("usage_log").insert(row).execute()

async def resolve_license_dep(
    request: Request,
    x_license_key: Optional[str] = Header(None, alias="X-License-Key"),
    authorization: Optional[str] = Header(None),
) -> LicenseInfo:
    """Dependency that resolves the license for the current request."""
    client_ip = request.client.host
    user_id = get_current_user(authorization)
    return await asyncio.to_thread(resolve_license, x_license_key, client_ip, user_id)


def save_env():
    """Persist current config values to the .env file.
    Semantic model content goes to a separate file (too large for .env)."""
    lines = [
        "# PBIChat — Environment Variables",
        "",
        f"OPENROUTER_API_KEY={OPENROUTER_API_KEY}",
        f"LLM_MODEL={LLM_MODEL}",
        "",
        f"EXTRA_CONTEXT={EXTRA_CONTEXT}",
        "",
        f"SETTINGS_PASSWORD={SETTINGS_PASSWORD}",
        "",
        f"SUPABASE_URL={SUPABASE_URL}",
        f"SUPABASE_KEY={SUPABASE_KEY}",
        "",
        f"SUPABASE_JWT_SECRET={SUPABASE_JWT_SECRET}",
        "",
        f"STRIPE_SECRET_KEY={STRIPE_SECRET_KEY}",
        f"STRIPE_PUBLISHABLE_KEY={STRIPE_PUBLISHABLE_KEY}",
        f"STRIPE_WEBHOOK_SECRET={STRIPE_WEBHOOK_SECRET}",
        f"STRIPE_PRICE_ID={STRIPE_PRICE_ID}",
        "",
    ]
    ENV_PATH.write_text("\n".join(lines))
    # Save semantic model to separate file
    SEMANTIC_MODEL_FILE.write_text(SEMANTIC_MODEL)


# ══════════════════════════════════════════════════════════
# TMDL CATALOG / SCHEMA PARSER
# ══════════════════════════════════════════════════════════
def _extract_source_from_block(text: str) -> dict:
    """Extract catalog, schema, and source table from an M/Power Query block.

    Handles two formats:
    1. Value.NativeQuery with SQL: "select * from catalog.schema.table ..."
    2. Navigation-style: Source{[Name="catalog",Kind="Database"]}[Data] →
       schema{[Name="schema",Kind="Schema"]}[Data] →
       table{[Name="view_name",Kind="View"]}[Data]
    """
    entry: dict = {}

    # Strategy 1: SQL string in Value.NativeQuery()
    sql_match = re.search(
        r'"select\s+\*\s+from\s+([^\s."]+)\.([^\s."]+)\.([^\s"]+)',
        text, re.IGNORECASE,
    )
    if sql_match:
        entry["catalog"] = sql_match.group(1)
        entry["schema"] = sql_match.group(2)
        entry["source_table"] = sql_match.group(3).split()[0].rstrip(")")
        return entry

    # Strategy 2: Navigation-style M expressions (DatabricksMultiCloud.Catalogs)
    # Extract catalog: {[Name="gold-poland-prod",Kind="Database"]}
    cat_nav = re.search(r'\[Name="([^"]+)",\s*Kind="Database"\]', text)
    if cat_nav:
        entry["catalog"] = cat_nav.group(1)

    # Extract schema: {[Name="iris",Kind="Schema"]}
    sch_nav = re.search(r'\[Name="([^"]+)",\s*Kind="Schema"\]', text)
    if sch_nav:
        entry["schema"] = sch_nav.group(1)

    # Extract source table/view: {[Name="vw_global_events",Kind="View"]} or Kind="Table"
    tbl_nav = re.search(r'\[Name="([^"]+)",\s*Kind="(?:View|Table)"\]', text)
    if tbl_nav:
        entry["source_table"] = tbl_nav.group(1)

    if entry:
        return entry

    # Strategy 3: Catalog/Database parameters in Databricks.Catalogs() call
    cat_match = re.search(r'Catalog\s*=\s*"([^"]+)"', text)
    if cat_match:
        entry["catalog"] = cat_match.group(1)
    db_match = re.search(r'Database\s*=\s*"([^"]+)"', text)
    if db_match:
        entry["schema"] = db_match.group(1)

    return entry


def parse_table_sources(tmdl_content: str) -> dict[str, dict]:
    """Parse TMDL content to extract which catalog.schema each table lives in.

    Scans M/Power Query expressions and partition source blocks for
    Databricks source info (catalog, schema, table/view name).

    Returns:
        { "TableName": { "catalog": "...", "schema": "...", "source_table": "..." } }
    """
    if not tmdl_content:
        return {}

    mappings: dict[str, dict] = {}

    # Split into file sections (=== filename ===)
    sections = re.split(r"===\s+(.+?)\s+===", tmdl_content)

    # sections = ['', 'filename1', 'content1', 'filename2', 'content2', ...]
    for i in range(1, len(sections), 2):
        filename = sections[i].strip()
        content = sections[i + 1] if i + 1 < len(sections) else ""

        # Find expression blocks: "expression <Name> = ..."
        for match in re.finditer(
            r"expression\s+([\w]+)\s*=\s*([\s\S]*?)(?=\nexpression\s|\n===|\Z)",
            content,
        ):
            entry = _extract_source_from_block(match.group(2))
            if entry:
                mappings[match.group(1)] = entry

        # Check table files for partition source blocks
        # Handles both "tables/Name.tmdl" and top-level "table Name" definitions
        if filename.startswith("tables/") and filename.endswith(".tmdl"):
            table_name = filename.replace("tables/", "").replace(".tmdl", "")
        else:
            # Check for "table <name>" definition at top of content
            tbl_def = re.match(r"table\s+(.+?)$", content.strip(), re.MULTILINE)
            table_name = tbl_def.group(1).strip().strip("'") if tbl_def else None

        if table_name and table_name not in mappings:
            # Look for partition source blocks
            partition_blocks = re.findall(
                r"partition\s+.+?=\s*m\s*\n\s*mode:\s*import\s*\n\s*source\s*=([\s\S]*?)(?=\n\t(?:annotation|column|measure)|\Z)",
                content,
            )
            for block in partition_blocks:
                entry = _extract_source_from_block(block)
                if entry:
                    mappings[table_name] = entry
                    break

    return mappings


# ══════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ══════════════════════════════════════════════════════════
class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    extra_context: Optional[str] = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message cannot be empty.")
        if len(v) > 10000:
            raise ValueError("Message too long (max 10,000 characters).")
        return v

class ChatResponse(BaseModel):
    response: str
    queries_executed: list[dict] = []  # {sql, result, error} for transparency
    tier: str = "free"
    daily_used: Optional[int] = None
    daily_limit: Optional[int] = None

class ConfigUpdate(BaseModel):
    openrouter_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    extra_context: Optional[str] = None

class TmdlFile(BaseModel):
    name: str
    content: str

class TmdlUploadRequest(BaseModel):
    files: list[TmdlFile]

class HealthResponse(BaseModel):
    status: str
    databricks_connected: bool
    llm_configured: bool

class WarehouseStatusResponse(BaseModel):
    state: str       # RUNNING, STARTING, STOPPED, NOT_CONFIGURED, ERROR, etc.
    name: str = ""
    message: str = ""
    ready: bool = False  # True only when state == RUNNING


# ══════════════════════════════════════════════════════════
# MULTI-CONNECTION SUPPORT
# ══════════════════════════════════════════════════════════
CONNECTIONS_FILE = Path(__file__).parent / "connections.json"
CONNECTIONS: dict[str, dict] = {}  # keyed by connection id


def _slugify(name: str) -> str:
    """Generate a URL-safe id from a connection name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "conn"


def load_connections():
    """Load connections from JSON file. Migrate from .env if needed."""
    global CONNECTIONS
    if CONNECTIONS_FILE.exists():
        try:
            data = json.loads(CONNECTIONS_FILE.read_text())
            for c in data.get("connections", []):
                if "id" in c:
                    CONNECTIONS[c["id"]] = c
        except (json.JSONDecodeError, KeyError):
            pass
    elif DATABRICKS_HOST:
        # Auto-migrate from existing .env DATABRICKS_* values
        conn = {
            "id": "databricks-default",
            "name": "Databricks",
            "type": "databricks",
            "host": DATABRICKS_HOST,
            "http_path": DATABRICKS_HTTP_PATH,
            "token": DATABRICKS_TOKEN,
            "catalog_schema": DATABRICKS_CATALOG_SCHEMA,
        }
        CONNECTIONS[conn["id"]] = conn
        save_connections()


def save_connections():
    """Persist connections to JSON file."""
    data = {"connections": list(CONNECTIONS.values())}
    CONNECTIONS_FILE.write_text(json.dumps(data, indent=2))


def get_first_databricks_conn() -> dict | None:
    """Return the first Databricks connection, or None."""
    for c in CONNECTIONS.values():
        if c.get("type") == "databricks":
            return c
    return None


# Load connections at startup
load_connections()


# ══════════════════════════════════════════════════════════
# SQL EXECUTION (multi-connection)
# ══════════════════════════════════════════════════════════
_BLOCKED_SQL = re.compile(
    r"^\s*(DROP|ALTER|TRUNCATE|DELETE|UPDATE|INSERT|CREATE|GRANT|REVOKE|EXEC|EXECUTE|MERGE)\b",
    re.IGNORECASE,
)

async def execute_sql(sql: str, connection_id: str = None) -> str:
    """Execute SQL against a named connection (or the first available one).
    Only SELECT, SHOW, DESCRIBE, and EXPLAIN statements are allowed."""
    if _BLOCKED_SQL.match(sql.strip()):
        return "ERROR: Only read-only queries (SELECT, SHOW, DESCRIBE) are allowed. Destructive operations are blocked."
    if connection_id and connection_id in CONNECTIONS:
        conn = CONNECTIONS[connection_id]
    elif CONNECTIONS:
        conn = next(iter(CONNECTIONS.values()))
    else:
        return "ERROR: No data connections configured."

    if conn.get("type") == "sqlserver":
        return await _execute_sqlserver_sql(sql, conn)
    return await _execute_databricks_sql(sql, conn)


async def _execute_databricks_sql(sql: str, conn: dict) -> str:
    """Execute a SQL statement against a Databricks connection."""
    host = conn.get("host", "")
    http_path = conn.get("http_path", "")
    token = conn.get("token", "")
    catalog_schema = conn.get("catalog_schema", "")

    if not host or not http_path or not token:
        return f"ERROR: Databricks connection '{conn.get('name', '')}' is not fully configured."

    url = f"{host.rstrip('/')}/api/2.0/sql/statements"
    warehouse_id = http_path.split("/")[-1]

    body = {
        "statement": sql,
        "warehouse_id": warehouse_id,
        "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE",
    }

    if catalog_schema:
        parts = catalog_schema.split(".")
        if len(parts) >= 1:
            body["catalog"] = parts[0]
        if len(parts) >= 2:
            body["schema"] = ".".join(parts[1:])

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        if resp.status_code != 200:
            error = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return f"ERROR: Databricks returned {resp.status_code}: {error.get('message', resp.text[:200])}"

        result = resp.json()

        # Poll if pending
        if result.get("status", {}).get("state") == "PENDING":
            stmt_id = result["statement_id"]
            for _ in range(30):
                await asyncio.sleep(2)
                poll = await client.get(
                    f"{url.replace('/statements', '/statements/' + stmt_id)}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                result = poll.json()
                if result.get("status", {}).get("state") != "PENDING":
                    break

        if result.get("status", {}).get("state") == "FAILED":
            err_msg = result.get("status", {}).get("error", {}).get("message", "Query failed")
            return f"ERROR: {err_msg}"

        # Format results
        cols = [c["name"] for c in result.get("manifest", {}).get("schema", {}).get("columns", [])]
        rows = result.get("result", {}).get("data_array", [])
        total = result.get("manifest", {}).get("total_row_count", len(rows))

        output = f"Columns: {', '.join(cols)}\nRows: {total}\n\n"
        output += " | ".join(cols) + "\n"
        output += " | ".join(["---"] * len(cols)) + "\n"

        for row in rows[:50]:
            output += " | ".join(str(v) if v is not None else "NULL" for v in row) + "\n"

        if len(rows) > 50:
            output += f"... ({total - 50} more rows)\n"

        return output


async def _execute_sqlserver_sql(sql: str, conn: dict) -> str:
    """Execute a SQL statement against a SQL Server connection."""
    try:
        import pymssql
    except ImportError:
        return "ERROR: pymssql is not installed. Run: pip install pymssql"

    server = conn.get("server", "")
    database = conn.get("database", "")
    username = conn.get("username", "")
    password = conn.get("password", "")

    if not server:
        return f"ERROR: SQL Server connection '{conn.get('name', '')}' has no server configured."

    def _run():
        with pymssql.connect(
            server=server,
            database=database or None,
            user=username or None,
            password=password or None,
        ) as c:
            with c.cursor() as cur:
                cur.execute(sql)
                if not cur.description:
                    return "Query executed successfully (no result set)."
                cols = [desc[0] for desc in cur.description]
                rows = cur.fetchmany(200)
                total = len(rows)
                output = f"Columns: {', '.join(cols)}\nRows: {total}\n\n"
                output += " | ".join(cols) + "\n"
                output += " | ".join(["---"] * len(cols)) + "\n"
                for row in rows[:50]:
                    output += " | ".join(str(v) if v is not None else "NULL" for v in row) + "\n"
                if total > 50:
                    output += f"... ({total - 50} more rows)\n"
                return output

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        # Scrub credentials from error messages
        msg = str(e)
        for secret in [conn.get("password", ""), conn.get("username", "")]:
            if secret and secret in msg:
                msg = msg.replace(secret, "***")
        return f"ERROR: {msg}"


# ══════════════════════════════════════════════════════════
# WAREHOUSE STATE CHECK (instant, never hangs)
# ══════════════════════════════════════════════════════════
_STATE_MESSAGES = {
    "RUNNING": "Warehouse is running and ready.",
    "STARTING": "Warehouse is starting up. This may take 2-5 minutes.",
    "STOPPED": "Warehouse is stopped. It will auto-start when a query is sent.",
    "STOPPING": "Warehouse is shutting down.",
    "DELETED": "Warehouse has been deleted.",
    "DELETING": "Warehouse is being deleted.",
}

async def get_warehouse_state(conn: dict = None) -> dict:
    """Check warehouse state via REST API. Accepts a specific Databricks connection dict."""
    if conn is None:
        conn = get_first_databricks_conn()
    if not conn:
        return {"state": "NOT_CONFIGURED", "name": "", "message": "No Databricks connections configured."}

    host = conn.get("host", "")
    http_path = conn.get("http_path", "")
    token = conn.get("token", "")

    if not host or not http_path or not token:
        return {"state": "NOT_CONFIGURED", "name": "", "message": "Databricks connection not fully configured."}

    warehouse_id = http_path.split("/")[-1]
    url = f"{host.rstrip('/')}/api/2.0/sql/warehouses/{warehouse_id}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 404:
                return {"state": "NOT_FOUND", "name": "", "message": f"Warehouse '{warehouse_id}' not found."}
            if resp.status_code == 403:
                return {"state": "FORBIDDEN", "name": "", "message": "Token lacks permission to view warehouse."}
            if resp.status_code != 200:
                return {"state": "ERROR", "name": "", "message": f"API returned {resp.status_code}"}

            data = resp.json()
            state = data.get("state", "UNKNOWN")
            return {
                "state": state,
                "name": data.get("name", ""),
                "message": _STATE_MESSAGES.get(state, f"Warehouse is in state: {state}"),
            }
    except httpx.TimeoutException:
        return {"state": "TIMEOUT", "name": "", "message": "Could not reach Databricks host."}
    except Exception as e:
        return {"state": "ERROR", "name": "", "message": str(e)}


# ══════════════════════════════════════════════════════════
# SCHEMA DISCOVERY
# ══════════════════════════════════════════════════════════
_schema_cache: dict[str, str] = {}

async def discover_schema() -> str:
    """Auto-discover tables and columns from all connections.
    Uses TMDL-parsed catalog mappings when available so tables in
    different catalogs are described with fully-qualified names."""
    global _schema_cache

    if not CONNECTIONS:
        return ""

    # Get catalog mappings from TMDL content
    table_sources = parse_table_sources(SEMANTIC_MODEL) if SEMANTIC_MODEL else {}
    all_schema = ""

    for conn_id, conn in CONNECTIONS.items():
        if conn_id in _schema_cache:
            all_schema += _schema_cache[conn_id]
            continue

        ctype = conn.get("type", "databricks")
        section = f"## Schema from [{conn.get('name', conn_id)}] (connection: `{conn_id}`, type: {ctype})\n\n"

        try:
            if ctype == "databricks":
                catalog_schema = conn.get("catalog_schema", "")
                sql = f"SHOW TABLES IN {catalog_schema}" if catalog_schema else "SHOW TABLES"
                tables_result = await execute_sql(sql, connection_id=conn_id)
                if tables_result.startswith("ERROR"):
                    section += f"(Discovery failed: {tables_result})\n\n"
                    _schema_cache[conn_id] = section
                    all_schema += section
                    continue

                lines = tables_result.split("\n")[3:]
                table_names = []
                for line in lines:
                    if not line.strip() or line.startswith("..."):
                        continue
                    cols = [c.strip() for c in line.split("|")]
                    name = cols[1] if len(cols) >= 2 else cols[0]
                    if name and name != "---":
                        table_names.append(name)

                for tn in table_names[:25]:
                    try:
                        prefix = f"{catalog_schema}." if catalog_schema else ""
                        desc = await execute_sql(f"DESCRIBE TABLE {prefix}{tn}", connection_id=conn_id)
                        section += f"### {tn}\n{desc}\n\n"
                    except Exception:
                        section += f"### {tn} (could not describe)\n\n"

                # Also describe tables from other catalogs found in TMDL mappings
                described = {tn.lower() for tn in table_names}
                for tname, info in table_sources.items():
                    if tname.lower() in described:
                        continue
                    src = info.get("source_table", tname)
                    cat = info.get("catalog", "")
                    sch = info.get("schema", "")
                    if cat and sch:
                        fqn = f"{cat}.{sch}.{src}"
                        try:
                            desc = await execute_sql(f"DESCRIBE TABLE {fqn}", connection_id=conn_id)
                            section += f"### {tname} (source: `{fqn}`)\n{desc}\n\n"
                            described.add(tname.lower())
                        except Exception:
                            section += f"### {tname} (source: `{fqn}` — could not describe)\n\n"

            elif ctype == "sqlserver":
                tables_result = await execute_sql(
                    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME",
                    connection_id=conn_id,
                )
                if tables_result.startswith("ERROR"):
                    section += f"(Discovery failed: {tables_result})\n\n"
                    _schema_cache[conn_id] = section
                    all_schema += section
                    continue

                lines = tables_result.split("\n")[3:]
                for line in lines[:25]:
                    if not line.strip() or line.startswith("..."):
                        continue
                    cols = [c.strip() for c in line.split("|")]
                    if len(cols) >= 2 and cols[0] != "---":
                        schema_name = cols[0]
                        table_name = cols[1]
                        try:
                            desc = await execute_sql(
                                f"SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='{schema_name}' AND TABLE_NAME='{table_name}' ORDER BY ORDINAL_POSITION",
                                connection_id=conn_id,
                            )
                            section += f"### [{schema_name}].[{table_name}]\n{desc}\n\n"
                        except Exception:
                            section += f"### [{schema_name}].[{table_name}] (could not describe)\n\n"

        except Exception as e:
            section += f"(Discovery failed: {e})\n\n"

        _schema_cache[conn_id] = section
        all_schema += section

    return all_schema


# ══════════════════════════════════════════════════════════
# SYSTEM PROMPT BUILDER
# ══════════════════════════════════════════════════════════
def _match_table_to_connection(table_info: dict) -> str:
    """Match a TMDL table source to a connection ID by comparing hostnames."""
    host = table_info.get("host", "")
    if not host:
        # Default to first Databricks connection
        db_conn = get_first_databricks_conn()
        return db_conn["id"] if db_conn else ""
    # Normalize and compare
    host_clean = host.replace("https://", "").replace("http://", "").rstrip("/").lower()
    for cid, conn in CONNECTIONS.items():
        if conn.get("type") != "databricks":
            continue
        conn_host = conn.get("host", "").replace("https://", "").replace("http://", "").rstrip("/").lower()
        if host_clean == conn_host or host_clean in conn_host or conn_host in host_clean:
            return cid
    # No match — return first Databricks
    db_conn = get_first_databricks_conn()
    return db_conn["id"] if db_conn else ""


def build_system_prompt(discovered_schema: str, extra_ctx: str = "", allowed_charts: Optional[set] = None) -> str:
    p = """You are "PBIChat" — a data query tool with live SQL access to one or more database connections.

## #1 RULE — DATA ONLY, ZERO OUTSIDE KNOWLEDGE (READ THIS FIRST)
- You are a DATA QUERY TOOL. You are NOT an analyst, consultant, or advisor.
- Your ONLY job: run SQL queries and report the numbers. NOTHING ELSE.
- EVERY sentence you write MUST come from a SQL query result. If it didn't come from a query, DO NOT WRITE IT.
- NEVER add: background info, project descriptions, strategic significance, industry context, technology descriptions, geographical info, company capabilities, or ANY knowledge from your training data.
- NEVER use phrases like: "This demonstrates...", "This showcases...", "This represents...", "This is significant because...", "This indicates...", "Key takeaways...", "In summary..."
- NEVER describe what a project IS, what it DOES, what technology it USES, or WHY it matters — unless those exact words appear in a SQL query result.
- If you catch yourself writing something that didn't come from a SQL result — DELETE IT before responding.
- VIOLATIONS: If your response contains ANY information not from a SQL result, it is WRONG and HARMFUL. The user makes business decisions from your answers. Outside knowledge pollutes the data.

## HOW IT WORKS
1. User asks a question about their data.
2. BEFORE writing any SQL, study the SEMANTIC MODEL section below to understand table relationships, join keys, cardinality, and DAX business logic. This is your primary reference for how tables connect.
3. If you need data, output SQL inside a ```sql_exec block. The system auto-executes it and feeds results back. If multiple connections exist, specify which one with `connection=<id>` on the opening line.
4. Report the query results factually. Do not add interpretation or context beyond what the data shows.
5. You can chain multiple queries (up to 5) if needed.

## ABSOLUTE RULE — NEVER ASSUME, ALWAYS VERIFY
- NEVER make assumptions about data values, filters, date ranges, or what the user means. If anything is ambiguous, ASK the user to clarify BEFORE running SQL.
- NEVER estimate, guess, or fabricate numbers. Every number you report MUST come from an executed SQL query result.
- If the user asks a question that could be interpreted multiple ways (e.g. "incidents on AP1000" — do they mean all time? This year? A specific incident type?), ASK which one they mean.
- If a query returns unexpected results (zero rows, very high/low numbers), tell the user what you found and ask if the filters are correct — do NOT silently adjust or re-interpret.
- ALWAYS show your reasoning: state what SQL you are running and why, so the user can verify your approach.
- When in doubt, run a simple exploratory query first (e.g. SELECT DISTINCT values) to understand the data before making aggregation queries.
- Your answers will be used for real business decisions. Wrong numbers are worse than no numbers. If you are not confident in a result, say so.

## CRITICAL RULES — TABLE SCOPE & CARDINALITY
- You may ONLY query tables that are explicitly defined in the SEMANTIC MODEL section below. If a table is not in the semantic model, DO NOT query it, reference it, or suggest it exists.
- If the user asks about data that does not map to any table in the semantic model, tell them: "That data is not available in the current model. The tables I can query are: [list them]."
- NEVER run SHOW TABLES, INFORMATION_SCHEMA, or any discovery queries. The semantic model is your single source of truth for what tables exist.
- NEVER guess or infer join keys from column names. Use ONLY the exact join columns defined in the semantic model relationships.
- STRICTLY follow the cardinality defined in each relationship. Pay special attention to:
  - "toCardinality: many" or "crossFilteringBehavior: bothDirections" = many-to-many. You MUST aggregate before or after joining to avoid row duplication. Never join many-to-many without GROUP BY.
  - Default relationships (no toCardinality specified) = many-to-one. The "fromColumn" side is the many side.
  - Getting cardinality wrong will produce INCORRECT row counts, duplicated metrics, and wrong totals. This is the #1 source of errors.
- When joining multiple tables, trace the full relationship path through the semantic model. Do not skip intermediate tables.
- Use table/column names exactly as they appear in the semantic model.
- Always use LIMIT (max 200) unless doing pure aggregation.
- Prefer aggregations (COUNT, SUM, AVG, GROUP BY) over raw row dumps.
- ALWAYS use fully-qualified three-part names (catalog.schema.table) for EVERY table in SQL. Refer to the TABLE SOURCE MAPPING section below to determine the correct catalog.schema for each table. Tables not listed in the mapping should use the default catalog.schema.
- NEVER assume all tables are in the same catalog. Different tables may live in different catalogs and schemas. Using the wrong catalog will cause query failures."""

    # Build connections table for the prompt
    if len(CONNECTIONS) > 1:
        p += "\n\n## AVAILABLE DATA CONNECTIONS\n"
        p += "You have access to multiple database connections. ALWAYS specify which connection to target.\n\n"
        p += "| ID | Name | Type | Default Catalog/Database | SQL Dialect |\n"
        p += "|---|---|---|---|---|\n"
        for cid, c in CONNECTIONS.items():
            ctype = c.get("type", "databricks")
            if ctype == "databricks":
                default_db = c.get("catalog_schema", "")
                dialect = "Databricks SQL (use `catalog`.`schema`.`table`)"
            else:
                default_db = c.get("database", "")
                dialect = "T-SQL (use [database].[schema].[table])"
            p += f"| {cid} | {c.get('name', '')} | {ctype} | {default_db} | {dialect} |\n"

        p += """
**SQL execution format** (MUST include connection= when multiple connections exist):
```sql_exec connection=<connection-id>
SELECT ...
```

- Use the correct SQL dialect for each connection type.
- Databricks: backtick quoting, three-part names `catalog`.`schema`.`table`
- SQL Server: square bracket quoting, [database].[schema].[table]
"""
    elif len(CONNECTIONS) == 1:
        c = next(iter(CONNECTIONS.values()))
        if c.get("type") == "databricks" and c.get("catalog_schema"):
            p += f"\n- Default catalog.schema: {c['catalog_schema']}"
        elif c.get("type") == "sqlserver" and c.get("database"):
            p += f"\n- Default database: {c['database']}"

    # Parse table source mappings from TMDL expressions
    table_sources = parse_table_sources(SEMANTIC_MODEL) if SEMANTIC_MODEL else {}

    # Semantic model goes FIRST so the AI reads relationships before schema/queries
    if SEMANTIC_MODEL:
        p += f"""

## SEMANTIC MODEL (from Power BI) — THIS IS YOUR ONLY SOURCE OF TRUTH
These are the ONLY tables you are allowed to query. Do not query any table not listed here.
Study every relationship, join key, and cardinality BEFORE writing any SQL.

**How to read TMDL format:**
- "fromColumn: TableA.ColX" → "toColumn: TableB.ColY" means TableA joins to TableB on those columns. Use ONLY these columns for JOINs.
- Default relationship (no toCardinality) = many-to-one. The fromColumn side has many rows per value.
- "toCardinality: many" = many-to-many. You MUST use GROUP BY / aggregation to avoid duplicated rows. Joining without aggregation WILL produce wrong numbers.
- "crossFilteringBehavior: bothDirections" = bidirectional filter. Both sides can filter each other — be careful with aggregation direction.
- DAX measures define the business logic in Power BI — replicate the equivalent logic in SQL when queried.
- Column definitions show data types, annotations, and summarization rules.
- Ignore LocalDateTable_* tables — these are auto-generated by Power BI for date hierarchies.

```
{SEMANTIC_MODEL}
```"""

    # Add table source mapping section so the AI knows which catalog.schema each table belongs to
    # Determine default catalog.schema from first Databricks connection
    _db_conn = get_first_databricks_conn()
    _default_cs = _db_conn.get("catalog_schema", "") if _db_conn else ""

    if table_sources:
        p += "\n\n## TABLE SOURCE MAPPING\n"
        p += "Each table below is mapped to its database source. ALWAYS use these fully-qualified names in SQL.\n\n"
        if len(CONNECTIONS) > 1:
            p += "| Table Name | Catalog | Schema | Source Table/View | Connection |\n"
            p += "|---|---|---|---|---|\n"
        else:
            p += "| Table Name | Catalog | Schema | Source Table/View |\n"
            p += "|---|---|---|---|\n"
        default_parts = _default_cs.split(".") if _default_cs else []
        default_cat = default_parts[0] if default_parts else "?"
        default_sch = default_parts[1] if len(default_parts) >= 2 else "?"
        for tname, info in sorted(table_sources.items()):
            cat = info.get("catalog", default_cat)
            sch = info.get("schema", default_sch)
            src = info.get("source_table", tname)
            if len(CONNECTIONS) > 1:
                # Try to match table host to a connection
                conn_id = _match_table_to_connection(info)
                p += f"| {tname} | {cat} | {sch} | `{cat}.{sch}.{src}` | {conn_id} |\n"
            else:
                p += f"| {tname} | {cat} | {sch} | `{cat}.{sch}.{src}` |\n"

        if _default_cs:
            p += f"\nFor any table NOT listed above, use the default: `{_default_cs}.<table_name>`\n"
    elif _default_cs:
        p += f"\n\n## TABLE SOURCE MAPPING\nNo explicit source mappings found in TMDL expressions. Use `{_default_cs}.<table_name>` for all tables.\n"

    if discovered_schema:
        p += "\n\n" + discovered_schema

    p += """

## CAPABILITIES
- Execute live SQL queries against connected databases and report results
- Write DAX measures and calculated columns for Power BI
- Explain data model relationships and how tables join

## CHART OUTPUT
When your analysis produces data that would be clearer as a visual, include a chart by outputting a ```chart code block with JSON inside. The frontend will render it as an interactive chart.

**Supported chart types:** """ + ", ".join(sorted(allowed_charts or ALL_CHART_TYPES)) + """

**Format:**
```chart
{
  "type": "bar",
  "title": "Descriptive Title",
  "labels": ["Category A", "Category B", "Category C"],
  "datasets": [
    { "label": "Series Name", "data": [45, 32, 18] }
  ]
}
```

**Multiple datasets** (for grouped/stacked bar, multi-line):
```chart
{
  "type": "line",
  "title": "Monthly Trend",
  "labels": ["Jan", "Feb", "Mar"],
  "datasets": [
    { "label": "Incidents", "data": [12, 8, 15] },
    { "label": "Near Misses", "data": [5, 3, 7] }
  ]
}
```

**When to include a chart:**
- Aggregations with 3+ categories (use bar or horizontalBar)
- Time series / trends (use line)
- Proportions / distributions (use pie or doughnut)
- Comparisons across groups (use bar)
- Correlations between two numeric fields (use scatter — datasets use `data: [{x: val, y: val}, ...]`)

**When NOT to chart:**
- Single scalar values (just state the number)
- Fewer than 3 data points (just describe them)
- Raw row listings (use a text table instead)

Always include a text summary alongside the chart — never output ONLY a chart with no explanation.

## STYLE
- Be concise. State the numbers and what query produced them. Nothing more.
- Do NOT editorialize. Do NOT add adjectives like "remarkable", "excellent", "impressive", "strong".
- Do NOT add sections like "Key Insights", "Strategic Significance", "What This Means", "Recommendations".
- Format: show the SQL you ran, the result, and a plain factual summary of what the numbers say. Stop there."""

    ctx = extra_ctx or EXTRA_CONTEXT
    if ctx:
        p += f"\n\n## ADDITIONAL CONTEXT\n{ctx}"

    if not CONNECTIONS:
        p += "\n\n## NOTE: No data connections configured. Help with general analytics, DAX, SQL concepts. Suggest adding connections via Settings for live data."
    elif not SEMANTIC_MODEL:
        p += "\n\n## NOTE: No semantic model loaded. You CANNOT query any tables until .tmdl files are loaded. Tell the user to load their semantic model via Settings > Load TMDL Files before asking data questions."

    # Final reminder at the end of prompt (LLMs weight beginning and end most heavily)
    p += """

## FINAL REMINDER — READ BEFORE EVERY RESPONSE
Before you send your response, review it sentence by sentence. Delete any sentence that did not come from a SQL query result. No exceptions. No "helpful context". No outside knowledge. Data only."""

    return p


# ══════════════════════════════════════════════════════════
# LLM API CALL (via OpenRouter)
# ══════════════════════════════════════════════════════════
async def call_llm(system: str, messages: list[dict]) -> str:
    """Call OpenRouter API and return the text response."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OpenRouter API key not configured.")

    # OpenRouter uses OpenAI-compatible format: system message goes in messages array
    or_messages = [{"role": "system", "content": system}] + messages

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={
                "model": LLM_MODEL,
                "max_tokens": 4096,
                "messages": or_messages,
            },
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
        )

        if resp.status_code != 200:
            error = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err_msg = error.get("error", {}).get("message", "") if isinstance(error.get("error"), dict) else str(error.get("error", ""))
            # Scrub any sensitive data from error messages
            safe_msg = err_msg[:200] if err_msg else f"HTTP {resp.status_code}"
            for secret in [OPENROUTER_API_KEY, SETTINGS_PASSWORD]:
                if secret and secret in safe_msg:
                    safe_msg = safe_msg.replace(secret, "***")
            raise HTTPException(
                status_code=502,
                detail=f"LLM API error: {safe_msg}",
            )

        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ══════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════
@app.post("/verify-password")
async def verify_password(request: Request, _auth=Depends(require_auth)):
    """Verify the password is correct. Returns 200 if valid, 403 if not."""
    _check_rate_limit(request.client.host)
    return {"status": "ok"}


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        databricks_connected=bool(CONNECTIONS),
        llm_configured=bool(OPENROUTER_API_KEY),
    )


@app.get("/warehouse-status", response_model=WarehouseStatusResponse)
async def warehouse_status(connection_id: str = None):
    """Check Databricks warehouse state (returns instantly, never hangs)."""
    conn = None
    if connection_id and connection_id in CONNECTIONS:
        conn = CONNECTIONS[connection_id]
    info = await get_warehouse_state(conn)
    state = info["state"]

    # If management API is blocked (FORBIDDEN), fall back to a direct SQL probe
    if state == "FORBIDDEN":
        cid = connection_id or (next(iter(CONNECTIONS)) if CONNECTIONS else None)
        result = await execute_sql("SELECT 1 AS test", connection_id=cid)
        if not result.startswith("ERROR"):
            return WarehouseStatusResponse(
                state="RUNNING", name="", message="Connected and ready.", ready=True,
            )

    return WarehouseStatusResponse(
        state=state,
        name=info.get("name", ""),
        message=info["message"],
        ready=state == "RUNNING",
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, _auth=Depends(require_auth), license: LicenseInfo = Depends(resolve_license_dep), authorization: Optional[str] = Header(None)):
    """Main chat endpoint — runs the agentic loop (authenticated, rate-limited, license-enforced)."""
    _check_rate_limit(request.client.host)
    chat_user_id = get_current_user(authorization)

    # License enforcement: daily query limit
    if license.daily_limit is not None and license.daily_used >= license.daily_limit:
        return ChatResponse(
            response=(
                f"You've reached the free tier limit of {license.daily_limit} queries per day. "
                "Upgrade to Pro for unlimited queries. Enter a license key in Settings."
            ),
            queries_executed=[],
            tier=license.tier,
            daily_used=license.daily_used,
            daily_limit=license.daily_limit,
        )

    # Pre-check: if any Databricks connection is configured, check warehouse state
    db_conn = get_first_databricks_conn()
    if db_conn:
        wh = await get_warehouse_state(db_conn)
        if wh["state"] in ("STOPPED", "STARTING", "STOPPING"):
            return ChatResponse(
                response=(
                    f"The Databricks warehouse is currently **{wh['state'].lower()}**. "
                    f"{wh['message']}\n\n"
                    "Please try again in a moment — the warehouse should be ready shortly."
                ),
                queries_executed=[],
                tier=license.tier, daily_used=license.daily_used, daily_limit=license.daily_limit,
            )
        if wh["state"] not in ("RUNNING", "FORBIDDEN") and wh["state"] not in ("NOT_CONFIGURED",):
            return ChatResponse(
                response=f"Databricks warehouse issue: {wh['message']}",
                queries_executed=[],
                tier=license.tier, daily_used=license.daily_used, daily_limit=license.daily_limit,
            )

    # Discover schema if not cached
    discovered = await discover_schema()

    # Build system prompt with tier-appropriate chart types
    system = build_system_prompt(
        discovered,
        extra_ctx=req.extra_context,
        allowed_charts=license.allowed_charts,
    )

    # Data-only reminder injected into every user message
    DATA_FENCE = "\n\n[REMINDER: Respond ONLY with data from SQL query results. Do NOT add outside knowledge, project descriptions, strategic significance, or any information not returned by a query. Every sentence must come from a SQL result.]"

    # Build messages
    messages = [{"role": m.role, "content": m.content} for m in req.history[-20:]]
    messages.append({"role": "user", "content": req.message + DATA_FENCE})

    queries_executed = []
    max_loops = 5

    for _ in range(max_loops):
        # Call LLM
        ai_text = await call_llm(system, messages)

        # Extract sql_exec blocks (with optional connection= parameter)
        sql_blocks = re.findall(r"```sql_exec(?:\s+connection=(\S+))?\n(.*?)```", ai_text, re.DOTALL)

        if not sql_blocks:
            # No SQL — final response; record usage
            await asyncio.to_thread(record_usage, license.key, request.client.host, "chat", chat_user_id)
            return ChatResponse(
                response=ai_text, queries_executed=queries_executed,
                tier=license.tier, daily_used=license.daily_used + 1, daily_limit=license.daily_limit,
            )

        # Execute each SQL query
        results_text = ""
        for i, (conn_id, sql) in enumerate(sql_blocks):
            sql = sql.strip()
            conn_id = conn_id.strip() if conn_id else None
            result = await execute_sql(sql, connection_id=conn_id)
            is_error = result.startswith("ERROR")
            queries_executed.append({"sql": sql, "result": result[:500], "error": is_error})
            results_text += f"\n\n### Query {i + 1}:\n```\n{sql}\n```\nResults:\n```\n{result}\n```"

        # Feed results back for next iteration
        messages.append({"role": "assistant", "content": ai_text})
        messages.append({
            "role": "user",
            "content": f"[SYSTEM: SQL query results]\n{results_text}\n\nReport these results factually. ONLY state what the numbers show — do NOT add background info, project descriptions, strategic significance, or any knowledge not in the query results above. If the data answers the question, present it and stop. Only output more sql_exec blocks if you need additional data.",
        })

    # If we exhausted loops, return last AI response; record usage
    await asyncio.to_thread(record_usage, license.key, request.client.host, "chat", chat_user_id)
    return ChatResponse(
        response=ai_text, queries_executed=queries_executed,
        tier=license.tier, daily_used=license.daily_used + 1, daily_limit=license.daily_limit,
    )


@app.post("/config")
async def update_config(req: ConfigUpdate, _auth=Depends(require_auth)):
    """Update runtime configuration (authenticated). Does NOT handle connections."""
    global OPENROUTER_API_KEY, LLM_MODEL, EXTRA_CONTEXT, _schema_cache

    if req.openrouter_api_key is not None:
        OPENROUTER_API_KEY = req.openrouter_api_key
    if req.llm_model is not None:
        LLM_MODEL = req.llm_model
    if req.extra_context is not None:
        EXTRA_CONTEXT = req.extra_context

    _schema_cache = {}
    save_env()
    return {"status": "updated"}


@app.get("/config")
async def get_config(_auth=Depends(require_auth)):
    """Retrieve current configuration (authenticated)."""
    return {
        "openrouter_api_key": OPENROUTER_API_KEY,
        "llm_model": LLM_MODEL,
        "semantic_model_loaded": bool(SEMANTIC_MODEL),
        "semantic_model_chars": len(SEMANTIC_MODEL),
        "extra_context": EXTRA_CONTEXT,
    }


# ── Connection CRUD endpoints ──

@app.get("/connections")
async def list_connections(_auth=Depends(require_auth)):
    """List all configured connections (secrets redacted)."""
    result = []
    for c in CONNECTIONS.values():
        d = dict(c)
        # Redact secrets for GET
        if d.get("token"):
            d["token"] = d["token"][:8] + "..."
        if d.get("password"):
            d["password"] = "***"
        result.append(d)
    return {"connections": result}


@app.post("/connections")
async def save_all_connections_endpoint(req: dict, _auth=Depends(require_auth), license: LicenseInfo = Depends(resolve_license_dep)):
    """Replace all connections (authenticated, license-enforced).
    Detects redacted secrets and preserves originals."""
    global CONNECTIONS, _schema_cache

    old_conns = dict(CONNECTIONS)  # snapshot before overwriting
    new_conns = req.get("connections", [])

    # License enforcement: connection limit
    if license.max_connections is not None and len(new_conns) > license.max_connections:
        raise HTTPException(
            status_code=403,
            detail=f"Free tier allows {license.max_connections} connection(s). "
                   f"You have {len(new_conns)}. Enter a Pro license key for unlimited connections."
        )
    CONNECTIONS = {}
    for c in new_conns:
        cid = c.get("id") or _slugify(c.get("name", "conn"))
        c["id"] = cid
        # Normalize Databricks host
        if c.get("type") == "databricks" and c.get("host"):
            h = c["host"].strip()
            if h and not h.startswith("http"):
                c["host"] = f"https://{h}"
        # Preserve redacted secrets from the old connection
        old = old_conns.get(cid, {})
        if c.get("token", "").endswith("...") and old.get("token"):
            c["token"] = old["token"]
        if c.get("password") == "***" and old.get("password"):
            c["password"] = old["password"]
        CONNECTIONS[cid] = c

    _schema_cache = {}
    save_connections()
    return {"status": "updated", "count": len(CONNECTIONS)}


@app.post("/test-connection/{connection_id}")
async def test_single_connection(connection_id: str, _auth=Depends(require_auth)):
    """Test a specific connection (authenticated)."""
    if connection_id not in CONNECTIONS:
        raise HTTPException(status_code=404, detail=f"Connection '{connection_id}' not found.")
    conn = CONNECTIONS[connection_id]

    if conn.get("type") == "databricks":
        result = await _execute_databricks_sql("SELECT 1 AS test", conn)
    elif conn.get("type") == "sqlserver":
        result = await _execute_sqlserver_sql("SELECT 1 AS test", conn)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown connection type '{conn.get('type')}'.")

    if result.startswith("ERROR"):
        raise HTTPException(status_code=500, detail=result)
    return {"status": "connected", "connection_id": connection_id}


@app.post("/upload-tmdl")
async def upload_tmdl(req: TmdlUploadRequest, _auth=Depends(require_auth)):
    """Accept uploaded .tmdl file contents and set as semantic model (authenticated)."""
    global SEMANTIC_MODEL, _schema_cache

    if not req.files:
        raise HTTPException(status_code=400, detail="No files provided.")

    # Filter out auto-generated / noise files
    _skip_prefixes = ("LocalDateTable_", "DateTableTemplate_")
    filtered = [
        f for f in req.files
        if not f.name.startswith("cultures/")
        and not any(Path(f.name).stem.startswith(p) for p in _skip_prefixes)
    ]

    parts = []
    for f in sorted(filtered, key=lambda x: x.name):
        parts.append(f"=== {f.name} ===\n{f.content}")

    SEMANTIC_MODEL = "\n\n".join(parts)
    _schema_cache = {}
    save_env()

    return {
        "status": "loaded",
        "files_loaded": len(filtered),
        "files_skipped": len(req.files) - len(filtered),
        "files": [f.name for f in sorted(filtered, key=lambda x: x.name)],
        "total_chars": len(SEMANTIC_MODEL),
    }


@app.post("/test-connection")
async def test_connection():
    """Test the first Databricks connection — fast check, no hanging."""
    conn = get_first_databricks_conn()
    if not conn:
        raise HTTPException(status_code=400, detail="No Databricks connections configured.")

    info = await get_warehouse_state(conn)
    state = info["state"]

    if state == "NOT_CONFIGURED":
        raise HTTPException(status_code=400, detail="Databricks not configured.")
    if state in ("DELETED", "DELETING", "NOT_FOUND"):
        raise HTTPException(status_code=500, detail=info["message"])
    if state in ("STOPPED", "STOPPING", "STARTING"):
        return {"status": "starting", "state": state, "message": info["message"]}

    # For RUNNING, FORBIDDEN, ERROR, TIMEOUT, or UNKNOWN — try SQL directly
    if state in ("RUNNING", "FORBIDDEN", "ERROR", "TIMEOUT") or state not in _STATE_MESSAGES:
        result = await execute_sql("SELECT 1 AS test", connection_id=conn["id"])
        if result.startswith("ERROR"):
            raise HTTPException(status_code=500, detail=result)
        return {"status": "connected", "state": "RUNNING", "message": "Connected and ready."}


# ══════════════════════════════════════════════════════════
# AUTH ENDPOINTS (Supabase Auth)
# ══════════════════════════════════════════════════════════
class AuthSignupRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""

class AuthLoginRequest(BaseModel):
    email: str
    password: str

class AuthRefreshRequest(BaseModel):
    refresh_token: str


@app.post("/auth/signup")
async def auth_signup(req: AuthSignupRequest):
    """Sign up a new user via Supabase Auth, create user row + license key."""
    if not _sb_users_ready:
        raise HTTPException(status_code=503, detail="User management tables not configured.")
    sb = _get_sb()
    try:
        auth_resp = sb.auth.sign_up({"email": req.email, "password": req.password})
    except Exception as e:
        msg = str(e)
        if "already registered" in msg.lower() or "already been registered" in msg.lower():
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise HTTPException(status_code=400, detail=msg[:200])

    user = auth_resp.user
    if not user:
        raise HTTPException(status_code=400, detail="Signup failed — no user returned.")

    license_key = f"pbi-{uuid.uuid4()}"
    try:
        sb.table("users").insert({
            "id": user.id,
            "email": req.email,
            "display_name": req.display_name or req.email.split("@")[0],
            "tier": "free",
            "license_key": license_key,
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"User row creation failed: {str(e)[:200]}")

    session = auth_resp.session
    return {
        "user_id": user.id,
        "email": req.email,
        "display_name": req.display_name or req.email.split("@")[0],
        "tier": "free",
        "license_key": license_key,
        "access_token": session.access_token if session else None,
        "refresh_token": session.refresh_token if session else None,
    }


@app.post("/auth/login")
async def auth_login(req: AuthLoginRequest):
    """Log in via Supabase Auth, return JWT + user profile."""
    if not _sb_users_ready:
        raise HTTPException(status_code=503, detail="User management tables not configured.")
    sb = _get_sb()
    try:
        auth_resp = sb.auth.sign_in_with_password({"email": req.email, "password": req.password})
    except Exception as e:
        msg = str(e)
        if "invalid" in msg.lower() or "credentials" in msg.lower():
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        raise HTTPException(status_code=400, detail=msg[:200])

    user = auth_resp.user
    session = auth_resp.session
    if not user or not session:
        raise HTTPException(status_code=401, detail="Login failed.")

    # Fetch user profile from users table
    try:
        profile_resp = sb.table("users").select("tier, license_key, display_name").eq("id", user.id).execute()
        if profile_resp.data:
            profile = profile_resp.data[0]
        else:
            # User exists in auth but not in users table — create row
            license_key = f"pbi-{uuid.uuid4()}"
            sb.table("users").insert({
                "id": user.id,
                "email": req.email,
                "display_name": req.email.split("@")[0],
                "tier": "free",
                "license_key": license_key,
            }).execute()
            profile = {"tier": "free", "license_key": license_key, "display_name": req.email.split("@")[0]}
    except Exception:
        profile = {"tier": "free", "license_key": "", "display_name": ""}

    return {
        "user_id": user.id,
        "email": req.email,
        "tier": profile["tier"],
        "license_key": profile["license_key"],
        "display_name": profile.get("display_name", ""),
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
    }


@app.post("/auth/refresh")
async def auth_refresh(req: AuthRefreshRequest):
    """Refresh the session using a refresh token."""
    sb = _get_sb()
    try:
        auth_resp = sb.auth.refresh_session(req.refresh_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    session = auth_resp.session
    if not session:
        raise HTTPException(status_code=401, detail="Session refresh failed.")

    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
    }


@app.get("/auth/me")
async def auth_me(user_id: str = Depends(require_user)):
    """Get current user profile + subscription status."""
    if not _sb_users_ready:
        raise HTTPException(status_code=503, detail="User management tables not configured.")
    sb = _get_sb()

    # Fetch user profile
    resp = sb.table("users").select("*").eq("id", user_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found.")
    user = resp.data[0]

    # Fetch active subscription
    sub_data = None
    try:
        sub_resp = sb.table("subscriptions") \
            .select("stripe_subscription_id, status, current_period_end, cancel_at_period_end") \
            .eq("user_id", user_id) \
            .in_("status", ["active", "trialing", "past_due"]) \
            .limit(1) \
            .execute()
        if sub_resp.data:
            sub_data = sub_resp.data[0]
    except Exception:
        pass

    return {
        "user_id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "tier": user["tier"],
        "license_key": user["license_key"],
        "subscription": sub_data,
        "created_at": user["created_at"],
    }


# ══════════════════════════════════════════════════════════
# STRIPE BILLING ENDPOINTS
# ══════════════════════════════════════════════════════════
@app.post("/billing/create-checkout-session")
async def billing_create_checkout(request: Request, user_id: str = Depends(require_user)):
    """Create a Stripe Checkout session for Pro subscription."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(status_code=503, detail="Stripe is not configured.")
    if not _sb_users_ready:
        raise HTTPException(status_code=503, detail="User management tables not configured.")

    sb = _get_sb()
    resp = sb.table("users").select("email, stripe_customer_id, tier").eq("id", user_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found.")
    user = resp.data[0]

    if user["tier"] == "pro":
        raise HTTPException(status_code=400, detail="You are already on the Pro plan.")

    # Get or create Stripe customer
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            email=user["email"],
            metadata={"user_id": user_id},
        )
        customer_id = customer.id
        sb.table("users").update({"stripe_customer_id": customer_id}).eq("id", user_id).execute()

    # Build success/cancel URLs (use Referer or Origin if available)
    origin = request.headers.get("origin", request.headers.get("referer", ""))
    if origin:
        origin = origin.rstrip("/")
    success_url = f"{origin}/?checkout=success" if origin else "https://pbichat.com/checkout-success"
    cancel_url = f"{origin}/?checkout=cancel" if origin else "https://pbichat.com/checkout-cancel"

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user_id},
    )

    return {"checkout_url": session.url, "session_id": session.id}


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    """Handle Stripe webhook events."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured.")

    body = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe signature.")

    try:
        event = stripe.Webhook.construct_event(body, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)[:200]}")

    sb = _get_sb()
    event_type = event["type"]
    data = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(sb, data)
        elif event_type == "invoice.paid":
            await _handle_invoice_paid(sb, data)
        elif event_type == "invoice.payment_failed":
            await _handle_invoice_failed(sb, data)
        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(sb, data)
        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(sb, data)
    except Exception as e:
        print(f"PBICHAT: Webhook handler error ({event_type}): {str(e)[:300]}")

    return JSONResponse({"status": "ok"})


async def _handle_checkout_completed(sb: SupabaseClient, session: dict):
    """checkout.session.completed — create subscription record, upgrade to pro."""
    user_id = session.get("metadata", {}).get("user_id")
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    if not user_id or not subscription_id:
        return

    # Fetch subscription details from Stripe
    sub = stripe.Subscription.retrieve(subscription_id)

    def _db():
        # Create subscription record
        sb.table("subscriptions").upsert({
            "user_id": user_id,
            "stripe_subscription_id": subscription_id,
            "stripe_price_id": sub["items"]["data"][0]["price"]["id"] if sub["items"]["data"] else STRIPE_PRICE_ID,
            "status": sub["status"],
            "current_period_start": datetime.fromtimestamp(sub["current_period_start"], tz=timezone.utc).isoformat(),
            "current_period_end": datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc).isoformat(),
            "cancel_at_period_end": sub.get("cancel_at_period_end", False),
        }, on_conflict="stripe_subscription_id").execute()

        # Upgrade user to pro
        update_data = {"tier": "pro", "updated_at": datetime.now(timezone.utc).isoformat()}
        if customer_id:
            update_data["stripe_customer_id"] = customer_id
        sb.table("users").update(update_data).eq("id", user_id).execute()

    await asyncio.to_thread(_db)
    print(f"PBICHAT: User {user_id} upgraded to pro via checkout.")


async def _handle_invoice_paid(sb: SupabaseClient, invoice: dict):
    """invoice.paid — record payment, ensure pro tier."""
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")
    if not customer_id:
        return

    def _db():
        # Find user by stripe_customer_id
        user_resp = sb.table("users").select("id").eq("stripe_customer_id", customer_id).execute()
        if not user_resp.data:
            return
        user_id = user_resp.data[0]["id"]

        # Record payment
        sb.table("payments").upsert({
            "user_id": user_id,
            "stripe_payment_id": invoice.get("payment_intent") or invoice["id"],
            "stripe_subscription_id": subscription_id,
            "amount_cents": invoice.get("amount_paid", 0),
            "currency": invoice.get("currency", "usd"),
            "status": invoice.get("status", "paid"),
        }, on_conflict="stripe_payment_id").execute()

        # Ensure pro tier
        sb.table("users").update({
            "tier": "pro",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()

    await asyncio.to_thread(_db)


async def _handle_invoice_failed(sb: SupabaseClient, invoice: dict):
    """invoice.payment_failed — downgrade to free."""
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    def _db():
        user_resp = sb.table("users").select("id").eq("stripe_customer_id", customer_id).execute()
        if not user_resp.data:
            return
        user_id = user_resp.data[0]["id"]
        sb.table("users").update({
            "tier": "free",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", user_id).execute()

    await asyncio.to_thread(_db)
    print(f"PBICHAT: Payment failed for customer {customer_id}, downgraded to free.")


async def _handle_subscription_updated(sb: SupabaseClient, sub: dict):
    """customer.subscription.updated — sync status."""
    subscription_id = sub.get("id")
    if not subscription_id:
        return

    def _db():
        update = {
            "status": sub["status"],
            "cancel_at_period_end": sub.get("cancel_at_period_end", False),
            "current_period_start": datetime.fromtimestamp(sub["current_period_start"], tz=timezone.utc).isoformat(),
            "current_period_end": datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if sub.get("canceled_at"):
            update["canceled_at"] = datetime.fromtimestamp(sub["canceled_at"], tz=timezone.utc).isoformat()

        sb.table("subscriptions").update(update).eq("stripe_subscription_id", subscription_id).execute()

        # If subscription is no longer active, downgrade
        if sub["status"] not in ("active", "trialing"):
            sub_resp = sb.table("subscriptions").select("user_id").eq("stripe_subscription_id", subscription_id).execute()
            if sub_resp.data:
                sb.table("users").update({
                    "tier": "free",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", sub_resp.data[0]["user_id"]).execute()

    await asyncio.to_thread(_db)


async def _handle_subscription_deleted(sb: SupabaseClient, sub: dict):
    """customer.subscription.deleted — cancel + downgrade."""
    subscription_id = sub.get("id")
    if not subscription_id:
        return

    def _db():
        now = datetime.now(timezone.utc).isoformat()
        sb.table("subscriptions").update({
            "status": "canceled",
            "canceled_at": now,
            "updated_at": now,
        }).eq("stripe_subscription_id", subscription_id).execute()

        sub_resp = sb.table("subscriptions").select("user_id").eq("stripe_subscription_id", subscription_id).execute()
        if sub_resp.data:
            sb.table("users").update({
                "tier": "free",
                "updated_at": now,
            }).eq("id", sub_resp.data[0]["user_id"]).execute()

    await asyncio.to_thread(_db)
    print(f"PBICHAT: Subscription {subscription_id} deleted, user downgraded.")


@app.post("/billing/cancel-subscription")
async def billing_cancel(user_id: str = Depends(require_user)):
    """Cancel the user's subscription at period end."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured.")
    if not _sb_users_ready:
        raise HTTPException(status_code=503, detail="User management tables not configured.")

    sb = _get_sb()
    sub_resp = sb.table("subscriptions") \
        .select("stripe_subscription_id, status") \
        .eq("user_id", user_id) \
        .in_("status", ["active", "trialing", "past_due"]) \
        .limit(1) \
        .execute()

    if not sub_resp.data:
        raise HTTPException(status_code=404, detail="No active subscription found.")

    subscription_id = sub_resp.data[0]["stripe_subscription_id"]

    try:
        stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel: {str(e)[:200]}")

    sb.table("subscriptions").update({
        "cancel_at_period_end": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("stripe_subscription_id", subscription_id).execute()

    return {"status": "canceled", "cancel_at_period_end": True}


# ══════════════════════════════════════════════════════════
# LICENSE STATUS (public — no auth required)
# ══════════════════════════════════════════════════════════
class LicenseStatusResponse(BaseModel):
    tier: str
    daily_used: int
    daily_limit: Optional[int]
    max_connections: Optional[int]
    allowed_charts: list[str]

@app.get("/license", response_model=LicenseStatusResponse)
async def get_license_status(license: LicenseInfo = Depends(resolve_license_dep)):
    """Check license tier and usage. No auth required — visual calls this on load."""
    return LicenseStatusResponse(
        tier=license.tier,
        daily_used=license.daily_used,
        daily_limit=license.daily_limit,
        max_connections=license.max_connections,
        allowed_charts=sorted(license.allowed_charts),
    )


# ══════════════════════════════════════════════════════════
# LICENSE ADMIN ENDPOINTS (authenticated)
# ══════════════════════════════════════════════════════════
class LicenseCreateRequest(BaseModel):
    label: str = ""
    tier: str = "pro"
    expires_at: Optional[str] = None  # ISO8601 or null
    max_connections: Optional[int] = None

class LicenseCreateResponse(BaseModel):
    key: str
    tier: str
    label: str
    created_at: str
    expires_at: Optional[str]

@app.post("/admin/licenses", response_model=LicenseCreateResponse)
async def create_license(req: LicenseCreateRequest, _auth=Depends(require_auth)):
    """Create a new license key (admin only)."""
    key = f"pbi-{uuid.uuid4()}"
    now = datetime.now(timezone.utc).isoformat()

    def _insert():
        sb = _get_sb()
        sb.table("licenses").insert({
            "key": key,
            "tier": req.tier,
            "label": req.label,
            "created_at": now,
            "expires_at": req.expires_at,
            "max_connections": req.max_connections,
        }).execute()

    await asyncio.to_thread(_insert)
    return LicenseCreateResponse(
        key=key, tier=req.tier, label=req.label,
        created_at=now, expires_at=req.expires_at,
    )

@app.get("/admin/licenses")
async def list_licenses(_auth=Depends(require_auth)):
    """List all license keys (admin only)."""
    def _query():
        sb = _get_sb()
        resp = sb.table("licenses") \
            .select("key, tier, label, created_at, expires_at, is_active, max_connections") \
            .order("created_at", desc=True) \
            .execute()
        return resp.data

    return {"licenses": await asyncio.to_thread(_query)}

@app.delete("/admin/licenses/{license_key}")
async def revoke_license(license_key: str, _auth=Depends(require_auth)):
    """Revoke (deactivate) a license key (admin only)."""
    def _revoke():
        sb = _get_sb()
        resp = sb.table("licenses") \
            .update({"is_active": False}) \
            .eq("key", license_key) \
            .execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="License key not found.")

    await asyncio.to_thread(_revoke)
    return {"status": "revoked", "key": license_key}

@app.get("/admin/usage")
async def get_usage_stats(days: int = 7, _auth=Depends(require_auth)):
    """Get usage stats for the last N days (admin only)."""
    def _query():
        sb = _get_sb()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        resp = sb.table("usage_log") \
            .select("license_key, created_at") \
            .gte("created_at", cutoff) \
            .execute()
        # Aggregate by license_key + day in Python (PostgREST doesn't support GROUP BY)
        from collections import Counter
        counts: Counter = Counter()
        for row in resp.data:
            day = row["created_at"][:10]
            counts[(row.get("license_key"), day)] += 1
        return [
            {"license_key": k, "day": d, "queries": c}
            for (k, d), c in sorted(counts.items(), key=lambda x: x[0][1], reverse=True)
        ]

    return {"usage": await asyncio.to_thread(_query)}
