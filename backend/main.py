"""
Bechtel PBIChat — Backend API
Orchestrates the agentic loop: User question -> LLM -> SQL -> Database -> LLM -> Response
Single-user, local-config backend. No Supabase dependency.
"""

import os
import re
import time
import logging
import asyncio
from collections import defaultdict, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from typing import Optional
import json

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

app = FastAPI(title="PBIChat API")

# ══════════════════════════════════════════════════════════
# CORS — restrict to your Power BI domain in production
# ══════════════════════════════════════════════════════════
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
# Read-only defaults from env — Azure OpenAI (Bechtel)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.2")

# ══════════════════════════════════════════════════════════
# LOCAL FILE-BASED CONFIG (replaces Supabase)
# ══════════════════════════════════════════════════════════
CONFIG_PATH = Path(__file__).parent / "config.json"
SEMANTIC_MODEL_PATH = Path(__file__).parent / "semantic_model.txt"


def _load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {"extra_context": "", "connections": [], "llm_model": ""}


def _save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def _load_semantic_model() -> str:
    try:
        if SEMANTIC_MODEL_PATH.exists():
            return SEMANTIC_MODEL_PATH.read_text()
    except Exception:
        pass
    return ""


def _save_semantic_model(content: str) -> None:
    SEMANTIC_MODEL_PATH.write_text(content)


# All chart types — licensing is handled by the visual via Microsoft's Licensing API.
ALL_CHART_TYPES = {"bar", "line", "pie", "doughnut", "scatter", "horizontalBar"}


# ══════════════════════════════════════════════════════════
# USER CONTEXT — loaded from local config files
# ══════════════════════════════════════════════════════════
@dataclass
class UserContext:
    user_id: str
    extra_context: str = ""
    connections: dict[str, dict] = field(default_factory=dict)
    llm_model: str = ""            # empty = use global default
    semantic_model: str = ""       # loaded from semantic_model.txt

    @property
    def effective_llm_model(self) -> str:
        return self.llm_model or LLM_MODEL


def get_user_context() -> UserContext:
    """Load user context from local config files."""
    config = _load_config()
    ctx = UserContext(
        user_id="local",
        extra_context=config.get("extra_context", ""),
        llm_model=config.get("llm_model", ""),
        semantic_model=_load_semantic_model(),
    )
    for c in config.get("connections", []):
        cid = c.get("id")
        if cid:
            ctx.connections[cid] = c
    return ctx


# ══════════════════════════════════════════════════════════
# SCHEMA CACHE (keyed by conn_id, 5-min TTL, LRU 1000)
# ══════════════════════════════════════════════════════════
_SCHEMA_CACHE_TTL = 300  # 5 minutes
_SCHEMA_CACHE_MAX = 1000

_user_schema_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _get_cached_schema(conn_id: str) -> str | None:
    """Return cached schema text if still valid, else None."""
    key = conn_id
    entry = _user_schema_cache.get(key)
    if entry is None:
        return None
    ts, text = entry
    if time.time() - ts > _SCHEMA_CACHE_TTL:
        _user_schema_cache.pop(key, None)
        return None
    # Move to end (most recently used)
    _user_schema_cache.move_to_end(key)
    return text


def _set_cached_schema(conn_id: str, text: str) -> None:
    """Cache schema text with current timestamp. Evict LRU if over limit."""
    key = conn_id
    _user_schema_cache[key] = (time.time(), text)
    _user_schema_cache.move_to_end(key)
    while len(_user_schema_cache) > _SCHEMA_CACHE_MAX:
        _user_schema_cache.popitem(last=False)


def _invalidate_all_schema() -> None:
    """Remove all cached schemas."""
    _user_schema_cache.clear()


# ══════════════════════════════════════════════════════════
# TMDL CATALOG / SCHEMA PARSER
# ══════════════════════════════════════════════════════════
def _extract_source_from_block(text: str) -> dict:
    """Extract catalog, schema, and source table from an M/Power Query block.

    Handles two formats:
    1. Value.NativeQuery with SQL: "select * from catalog.schema.table ..."
    2. Navigation-style: Source{[Name="catalog",Kind="Database"]}[Data] ->
       schema{[Name="schema",Kind="Schema"]}[Data] ->
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
    inline_data: Optional[str] = None   # CSV from Power BI field wells
    inline_stats: Optional[str] = None  # JSON summary stats from ALL rows

    @field_validator("inline_data")
    @classmethod
    def inline_data_limit(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 500000:
            raise ValueError("Inline data too large (max 500,000 characters).")
        return v

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

class ConfigUpdate(BaseModel):
    llm_model: Optional[str] = None
    extra_context: Optional[str] = None

class TmdlFile(BaseModel):
    name: str
    content: str

class TmdlUploadRequest(BaseModel):
    files: list[TmdlFile]
    connections: Optional[list] = None  # Per-report connections saved alongside the model

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
# MULTI-CONNECTION SUPPORT (loaded from local config)
# ══════════════════════════════════════════════════════════


def _slugify(name: str) -> str:
    """Generate a URL-safe id from a connection name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "conn"


# ══════════════════════════════════════════════════════════
# SQL EXECUTION (multi-connection)
# ══════════════════════════════════════════════════════════
_BLOCKED_SQL = re.compile(
    r"^\s*(DROP|ALTER|TRUNCATE|DELETE|UPDATE|INSERT|CREATE|GRANT|REVOKE|EXEC|EXECUTE|MERGE)\b",
    re.IGNORECASE,
)

async def execute_sql(sql: str, connections: dict[str, dict] = None, connection_id: str = None) -> str:
    """Execute SQL against a named connection (or the first available one).
    Only SELECT, SHOW, DESCRIBE, and EXPLAIN statements are allowed."""
    conns = connections or {}
    if _BLOCKED_SQL.match(sql.strip()):
        return "ERROR: Only read-only queries (SELECT, SHOW, DESCRIBE) are allowed. Destructive operations are blocked."
    if connection_id and connection_id in conns:
        conn = conns[connection_id]
    elif conns:
        conn = next(iter(conns.values()))
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

            # Auto-start warehouse if stopped
            if state == "STOPPED":
                try:
                    start_url = f"{host.rstrip('/')}/api/2.0/sql/warehouses/{warehouse_id}/start"
                    await client.post(start_url, headers={"Authorization": f"Bearer {token}"})
                    logging.info(f"Sent START command to warehouse {warehouse_id}")
                    state = "STARTING"
                except Exception as e:
                    logging.warning(f"Failed to start warehouse {warehouse_id}: {e}")

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
async def discover_schema(ctx: UserContext) -> str:
    """Auto-discover tables and columns from all connections.
    Uses TMDL-parsed catalog mappings when available so tables in
    different catalogs are described with fully-qualified names."""
    if not ctx.connections:
        return ""

    # Get catalog mappings from TMDL content
    table_sources = parse_table_sources(ctx.semantic_model) if ctx.semantic_model else {}
    all_schema = ""

    for conn_id, conn in ctx.connections.items():
        cached = _get_cached_schema(conn_id)
        if cached is not None:
            all_schema += cached
            continue

        ctype = conn.get("type", "databricks")
        section = f"## Schema from [{conn.get('name', conn_id)}] (connection: `{conn_id}`, type: {ctype})\n\n"

        try:
            if ctype == "databricks":
                catalog_schema = conn.get("catalog_schema", "")
                sql = f"SHOW TABLES IN {catalog_schema}" if catalog_schema else "SHOW TABLES"
                tables_result = await execute_sql(sql, connections=ctx.connections, connection_id=conn_id)
                if tables_result.startswith("ERROR"):
                    section += f"(Discovery failed: {tables_result})\n\n"
                    _set_cached_schema(conn_id, section)
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
                        desc = await execute_sql(f"DESCRIBE TABLE {prefix}{tn}", connections=ctx.connections, connection_id=conn_id)
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
                            desc = await execute_sql(f"DESCRIBE TABLE {fqn}", connections=ctx.connections, connection_id=conn_id)
                            section += f"### {tname} (source: `{fqn}`)\n{desc}\n\n"
                            described.add(tname.lower())
                        except Exception:
                            section += f"### {tname} (source: `{fqn}` -- could not describe)\n\n"

            elif ctype == "sqlserver":
                tables_result = await execute_sql(
                    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME",
                    connections=ctx.connections,
                    connection_id=conn_id,
                )
                if tables_result.startswith("ERROR"):
                    section += f"(Discovery failed: {tables_result})\n\n"
                    _set_cached_schema(conn_id, section)
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
                                connections=ctx.connections,
                                connection_id=conn_id,
                            )
                            section += f"### [{schema_name}].[{table_name}]\n{desc}\n\n"
                        except Exception:
                            section += f"### [{schema_name}].[{table_name}] (could not describe)\n\n"

        except Exception as e:
            section += f"(Discovery failed: {e})\n\n"

        _set_cached_schema(conn_id, section)
        all_schema += section

    return all_schema


# ══════════════════════════════════════════════════════════
# SYSTEM PROMPT BUILDER
# ══════════════════════════════════════════════════════════
def _match_table_to_connection(table_info: dict, connections: dict[str, dict]) -> str:
    """Match a TMDL table source to a connection ID by comparing hostnames."""
    host = table_info.get("host", "")
    if not host:
        # Default to first Databricks connection
        for cid, c in connections.items():
            if c.get("type") == "databricks":
                return cid
        return ""
    # Normalize and compare
    host_clean = host.replace("https://", "").replace("http://", "").rstrip("/").lower()
    for cid, conn in connections.items():
        if conn.get("type") != "databricks":
            continue
        conn_host = conn.get("host", "").replace("https://", "").replace("http://", "").rstrip("/").lower()
        if host_clean == conn_host or host_clean in conn_host or conn_host in host_clean:
            return cid
    # No match -- return first Databricks
    for cid, c in connections.items():
        if c.get("type") == "databricks":
            return cid
    return ""


# Regex for culture/locale TMDL files: "en-US.tmdl", "fr-FR.tmdl", etc.
_CULTURE_FILE_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?\.tmdl$")

# Lines to strip from TMDL content (not useful for SQL generation)
_TMDL_STRIP_RE = re.compile(
    r"^\s*(lineageTag:|sourceLineageTag:|summarizeBy:|annotation |changedProperty )",
)


def _slim_tmdl(raw: str, max_chars: int = 400_000) -> str:
    """Reduce TMDL content size for the LLM system prompt.

    1. Remove entire culture file sections (en-US.tmdl, fr-FR.tmdl, etc.)
    2. Strip non-essential metadata lines (lineageTag, annotation, summarizeBy)
    3. Collapse consecutive blank lines
    4. Truncate to max_chars if still too large
    """
    # Remove culture file sections
    sections = re.split(r"(^=== .+? ===$)", raw, flags=re.MULTILINE)
    kept: list[str] = []
    skip = False
    for part in sections:
        if part.startswith("=== ") and part.endswith(" ==="):
            fname = part[4:-4].split("/")[-1]  # handle both "cultures/en-US.tmdl" and "en-US.tmdl"
            if _CULTURE_FILE_RE.match(fname):
                skip = True
                continue
            skip = False
            kept.append(part)
        elif not skip:
            kept.append(part)
    joined = "".join(kept)

    # Strip metadata lines and collapse blank lines
    out: list[str] = []
    prev_blank = False
    for line in joined.split("\n"):
        if _TMDL_STRIP_RE.match(line):
            continue
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out.append(line)
    result = "\n".join(out)

    # Hard truncate if still too large
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n... (model truncated -- upload fewer tables for full context)"

    return result


def build_system_prompt(ctx: UserContext, discovered_schema: str, allowed_charts: Optional[set] = None) -> str:
    p = """You are "PBIChat" -- a data query tool with live SQL access to one or more database connections.

## #1 RULE -- DATA ONLY, ZERO OUTSIDE KNOWLEDGE (READ THIS FIRST)
- You are a DATA QUERY TOOL. You are NOT an analyst, consultant, or advisor.
- Your ONLY job: run SQL queries and report the numbers. NOTHING ELSE.
- EVERY sentence you write MUST come from a SQL query result. If it didn't come from a query, DO NOT WRITE IT.
- NEVER add: background info, project descriptions, strategic significance, industry context, technology descriptions, geographical info, company capabilities, or ANY knowledge from your training data.
- NEVER use phrases like: "This demonstrates...", "This showcases...", "This represents...", "This is significant because...", "This indicates...", "Key takeaways...", "In summary..."
- NEVER describe what a project IS, what it DOES, what technology it USES, or WHY it matters -- unless those exact words appear in a SQL query result.
- If you catch yourself writing something that didn't come from a SQL result -- DELETE IT before responding.
- VIOLATIONS: If your response contains ANY information not from a SQL result, it is WRONG and HARMFUL. The user makes business decisions from your answers. Outside knowledge pollutes the data.

## HOW IT WORKS
1. User asks a question about their data.
2. BEFORE writing any SQL, study the SEMANTIC MODEL section below to understand table relationships, join keys, cardinality, and DAX business logic. This is your primary reference for how tables connect.
3. If you need data, output SQL inside a ```sql_exec block. The system auto-executes it and feeds results back. If multiple connections exist, specify which one with `connection=<id>` on the opening line.
4. Report the query results factually. Do not add interpretation or context beyond what the data shows.
5. You can chain multiple queries (up to 5) if needed.

## ABSOLUTE RULE -- ANSWER ONLY, HIDE YOUR WORK
- NEVER show SQL queries, code blocks, or internal reasoning to the user. The user only wants the ANSWER.
- NEVER say "Let me run a query", "I need to query", "Let me check", or describe your process. Just DO IT silently and present the results.
- Your output should look like a polished report -- charts, tables, metric cards -- NOT a developer console.
- The ```sql_exec blocks are for the SYSTEM to execute. They are YOUR internal tool. NEVER include them in your visible response text.
- NEVER make assumptions about data values, filters, date ranges, or what the user means. If anything is ambiguous, ASK the user to clarify BEFORE running SQL.
- NEVER estimate, guess, or fabricate numbers. Every number you report MUST come from an executed SQL query result.
- If the user asks a question that could be interpreted multiple ways, ASK which one they mean.
- If a query returns unexpected results (zero rows, very high/low numbers), tell the user what you found and ask if the filters are correct.
- When in doubt, run a simple exploratory query first (e.g. SELECT DISTINCT values) to understand the data before making aggregation queries.
- Your answers will be used for real business decisions. Wrong numbers are worse than no numbers. If you are not confident in a result, say so.

## CRITICAL RULES -- TABLE SCOPE & CARDINALITY
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
    connections = ctx.connections
    semantic_model = _slim_tmdl(ctx.semantic_model) if ctx.semantic_model else ""

    if len(connections) > 1:
        p += "\n\n## AVAILABLE DATA CONNECTIONS\n"
        p += "You have access to multiple database connections. ALWAYS specify which connection to target.\n\n"
        p += "| ID | Name | Type | Default Catalog/Database | SQL Dialect |\n"
        p += "|---|---|---|---|---|\n"
        for cid, c in connections.items():
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
    elif len(connections) == 1:
        c = next(iter(connections.values()))
        if c.get("type") == "databricks" and c.get("catalog_schema"):
            p += f"\n- Default catalog.schema: {c['catalog_schema']}"
        elif c.get("type") == "sqlserver" and c.get("database"):
            p += f"\n- Default database: {c['database']}"

    # Parse table source mappings from TMDL expressions
    table_sources = parse_table_sources(semantic_model) if semantic_model else {}

    # Semantic model goes FIRST so the AI reads relationships before schema/queries
    if semantic_model:
        p += f"""

## SEMANTIC MODEL (from Power BI) -- THIS IS YOUR ONLY SOURCE OF TRUTH
These are the ONLY tables you are allowed to query. Do not query any table not listed here.
Study every relationship, join key, and cardinality BEFORE writing any SQL.

**How to read TMDL format:**
- "fromColumn: TableA.ColX" -> "toColumn: TableB.ColY" means TableA joins to TableB on those columns. Use ONLY these columns for JOINs.
- Default relationship (no toCardinality) = many-to-one. The fromColumn side has many rows per value.
- "toCardinality: many" = many-to-many. You MUST use GROUP BY / aggregation to avoid duplicated rows. Joining without aggregation WILL produce wrong numbers.
- "crossFilteringBehavior: bothDirections" = bidirectional filter. Both sides can filter each other -- be careful with aggregation direction.
- DAX measures define the business logic in Power BI -- replicate the equivalent logic in SQL when queried.
- Column definitions show data types, annotations, and summarization rules.
- Ignore LocalDateTable_* tables -- these are auto-generated by Power BI for date hierarchies.

```
{semantic_model}
```"""

    # Add table source mapping section so the AI knows which catalog.schema each table belongs to
    # Determine default catalog.schema from first Databricks connection
    _default_cs = ""
    for _c in connections.values():
        if _c.get("type") == "databricks" and _c.get("catalog_schema"):
            _default_cs = _c["catalog_schema"]
            break

    if table_sources:
        p += "\n\n## TABLE SOURCE MAPPING\n"
        p += "Each table below is mapped to its database source. ALWAYS use these fully-qualified names in SQL.\n\n"
        if len(connections) > 1:
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
            if len(connections) > 1:
                # Try to match table host to a connection
                conn_id = _match_table_to_connection(info, connections)
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

## PRESENTATION -- ALWAYS VISUAL-FIRST
Your responses should look like a modern data dashboard, not a wall of text. Every answer should be visually rich and easy to scan.

**Rules:**
1. **ALWAYS include a chart** when query results have 2+ rows. Default to a chart -- only skip it for a single scalar number.
2. **Use bold metric cards** for key numbers. Format: **Total Incidents** 1,247 | **Average/Month** 104 | **Peak** March (187). Place these ABOVE any chart or table.
3. **Use markdown tables** for detailed breakdowns -- the frontend renders them as styled data tables. Always format tables with pipes and headers:
| Column A | Column B | Column C |
|---|---|---|
| value 1 | value 2 | value 3 |
4. **Combine formats**: lead with metric cards for the headline numbers, then a chart for the visual, then a table for row-level detail.
5. **Use headings** (`###`) to organize multi-part answers into clear sections.
6. **Format numbers** for readability: use commas for thousands (1,247), round percentages to 1 decimal (73.2%), and use currency symbols where appropriate.
7. **Never output raw data dumps**. If a query returns rows, present them in a table or chart -- never as plain text lists.

**Example response structure:**
### Monthly Incidents
**Total** 1,247 | **Average/Month** 104 | **Peak** March (187)

```chart
{"type": "bar", "title": "Incidents by Month", "labels": [...], "datasets": [...]}
```

## CHART OUTPUT
Include a chart by outputting a ```chart code block with JSON. The frontend renders it as an interactive Chart.js visual.

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

**Chart type guide:**
- Aggregations / comparisons across groups -> **bar** or **horizontalBar**
- Time series / trends -> **line**
- Proportions / parts of a whole -> **pie** or **doughnut**
- Two numeric variables -> **scatter** (datasets use `data: [{x: val, y: val}, ...]`)

**When NOT to chart:**
- A single scalar value (use a bold metric card instead)
- Raw row listings with no aggregation (use a markdown table)

## STYLE
- Be concise and visual. Lead with the numbers, support with charts.
- Do NOT editorialize. No adjectives like "remarkable", "excellent", "impressive", "strong".
- Do NOT add "Key Insights", "Strategic Significance", "What This Means", "Recommendations".
- Every number must come from a SQL query result. Present the results visually -- never show the SQL itself."""

    if ctx.extra_context:
        p += f"\n\n## ADDITIONAL CONTEXT\n{ctx.extra_context}"

    if not connections and semantic_model:
        p += """

## NOTE: SCHEMA-ONLY MODE -- No database connections configured.
You have the semantic model (table definitions, columns, relationships, measures) but CANNOT execute SQL queries.
You CAN:
- Explain the data model structure, tables, columns, and relationships
- Suggest DAX measures and calculated columns
- Write SQL queries the user could run (but you cannot execute them)
- Analyze the schema to answer questions about what data is available
- Help the user understand their data model
You CANNOT: Execute queries or return actual data values. If the user asks for data, explain that they need to add a database connection via Settings for live queries.
Do NOT generate sql_exec blocks -- they will fail without a connection."""
    elif not connections:
        p += "\n\n## NOTE: No data connections and no semantic model loaded. Help with general analytics, DAX, SQL concepts. Suggest adding a semantic model (.tmdl files) and connections via Settings."
    elif not semantic_model:
        p += "\n\n## NOTE: No semantic model loaded. You CANNOT query any tables until .tmdl files are loaded. Tell the user to load their semantic model via Settings > Load TMDL Files before asking data questions."

    # Final reminder at the end of prompt (LLMs weight beginning and end most heavily)
    p += """

## FINAL REMINDER -- READ BEFORE EVERY RESPONSE
Before you send your response, review it sentence by sentence. Delete any sentence that did not come from a SQL query result. No exceptions. No "helpful context". No outside knowledge. Data only."""

    return p


def build_inline_system_prompt(ctx: UserContext, inline_data: str, inline_stats: str | None = None, allowed_charts: set | None = None) -> str:
    """System prompt for inline data mode -- analyze CSV + pre-computed stats, no SQL."""
    charts = ", ".join(sorted(allowed_charts or ALL_CHART_TYPES))
    p = '''You are "PBIChat" -- an AI data analyst. The user provided a dataset from Power BI.

## HOW TO USE THE DATA
You have TWO data sources below:
1. **SUMMARY STATISTICS** -- Pre-computed from ALL rows in the dataset. Use these for aggregate questions (totals, averages, counts, min/max). These are ALWAYS accurate.
2. **RAW DATA (CSV)** -- The actual rows. May be truncated if the dataset is large. Use this for detail questions (finding specific records, top/bottom N, filtering, listing).

**CRITICAL**: For any question about totals, sums, averages, counts, or min/max -- use the SUMMARY STATISTICS, not the CSV. The CSV may be a subset of the full data.

## RULES
- NEVER fabricate numbers. Every value must come from the stats or CSV.
- If a question requires row-level detail and the CSV is truncated, tell the user you can only analyze the rows visible.
- Do NOT generate SQL. Analyze the provided data directly.
'''

    if inline_stats:
        p += f"\n## SUMMARY STATISTICS (computed from ALL rows)\n```json\n{inline_stats}\n```\n"

    p += f"\n## RAW DATA (CSV)\n```csv\n{inline_data}\n```\n"

    p += f"""
## PRESENTATION -- VISUAL-FIRST
1. ALWAYS include a chart when data has 2+ categories.
2. Use **bold metric cards**: **Total** 1,247 | **Average** 104 | **Peak** March (187)
3. Use markdown tables for detailed breakdowns.
4. Format numbers: commas, 1-decimal percentages, currency symbols.
5. Use ### headings to organize multi-part answers.

## CHART OUTPUT
Output a ```chart code block with JSON:
```chart
{{"type":"bar","title":"Title","labels":["A","B"],"datasets":[{{"label":"Series","data":[45,32]}}]}}
```
Supported types: {charts}
- Comparisons: bar/horizontalBar | Trends: line | Proportions: pie/doughnut | Correlation: scatter
"""
    if ctx.extra_context:
        p += f"\n## ADDITIONAL CONTEXT\n{ctx.extra_context}\n"
    return p


# ══════════════════════════════════════════════════════════
# LLM API CALL (via Azure OpenAI -- Bechtel)
# ══════════════════════════════════════════════════════════
async def call_llm(system: str, messages: list[dict], ctx: UserContext = None) -> str:
    """Call Azure OpenAI API and return the text response.
    Uses ctx for per-user model overrides when provided."""
    api_key = AZURE_OPENAI_API_KEY
    endpoint = AZURE_OPENAI_ENDPOINT
    model = ctx.effective_llm_model if ctx else LLM_MODEL

    if not api_key or not endpoint:
        raise HTTPException(status_code=500, detail="Azure OpenAI API key/endpoint not configured.")

    # Azure OpenAI uses OpenAI-compatible chat format
    api_messages = [{"role": "system", "content": system}] + messages

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            endpoint,
            json={
                "model": model,
                "max_tokens": 4096,
                "messages": api_messages,
                "reasoning_effort": "none",
            },
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
            },
        )

        if resp.status_code != 200:
            error = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err_msg = error.get("error", {}).get("message", "") if isinstance(error.get("error"), dict) else str(error.get("error", ""))
            # Scrub any sensitive data from error messages
            safe_msg = err_msg[:200] if err_msg else f"HTTP {resp.status_code}"
            if api_key and api_key in safe_msg:
                safe_msg = safe_msg.replace(api_key, "***")
            # Log the prompt size for debugging context-window issues
            total_chars = sum(len(m.get("content", "")) for m in api_messages)
            logging.error(f"LLM API error ({resp.status_code}): {safe_msg} | model={model} prompt_chars={total_chars}")
            raise HTTPException(
                status_code=502,
                detail=f"LLM API error: {safe_msg}",
            )

        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ══════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        databricks_connected=True,
        llm_configured=bool(AZURE_OPENAI_API_KEY),
    )


@app.get("/warehouse-status", response_model=WarehouseStatusResponse)
async def warehouse_status(connection_id: str = None):
    """Check Databricks warehouse state (returns instantly, never hangs)."""
    ctx = get_user_context()
    user_conns = ctx.connections

    if not user_conns:
        return WarehouseStatusResponse(
            state="RUNNING", name="", message="Backend is running.", ready=True,
        )

    conn = None
    if connection_id and connection_id in user_conns:
        conn = user_conns[connection_id]
    elif not conn:
        # Find first Databricks connection
        for c in user_conns.values():
            if c.get("type") == "databricks":
                conn = c
                break

    if not conn:
        return WarehouseStatusResponse(
            state="RUNNING", name="", message="No Databricks connections configured.", ready=True,
        )

    info = await get_warehouse_state(conn)
    state = info["state"]

    # If management API is blocked (FORBIDDEN), fall back to a direct SQL probe
    if state == "FORBIDDEN":
        cid = connection_id or (next(iter(user_conns)) if user_conns else None)
        result = await execute_sql("SELECT 1 AS test", connections=user_conns, connection_id=cid)
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
async def chat(req: ChatRequest, request: Request):
    """Main chat endpoint -- runs the agentic loop (rate-limited by IP)."""
    _check_rate_limit(request.client.host)
    ctx = get_user_context()

    # -- INLINE DATA MODE --
    if req.inline_data:
        chat_ctx = ctx
        if req.extra_context:
            chat_ctx = UserContext(
                user_id=ctx.user_id, extra_context=req.extra_context,
                connections=ctx.connections,
                llm_model=ctx.llm_model,
                semantic_model=ctx.semantic_model,
            )
        system = build_inline_system_prompt(
            chat_ctx, req.inline_data,
            inline_stats=req.inline_stats,
            allowed_charts=ALL_CHART_TYPES,
        )
        logging.info(f"Inline data mode: {len(req.inline_data)} chars CSV, stats={'yes' if req.inline_stats else 'no'}, system_prompt={len(system)} chars")
        messages = [{"role": m.role, "content": m.content} for m in req.history[-20:]]
        messages.append({"role": "user", "content": req.message})
        ai_text = await call_llm(system, messages, ctx=ctx)
        return ChatResponse(response=ai_text, queries_executed=[])

    # -- DATABASE MODE --

    # Pre-check: if any Databricks connection is configured, check warehouse state
    db_conn = None
    for c in ctx.connections.values():
        if c.get("type") == "databricks":
            db_conn = c
            break
    if db_conn:
        wh = await get_warehouse_state(db_conn)
        if wh["state"] in ("STOPPED", "STARTING", "STOPPING"):
            return ChatResponse(
                response=(
                    f"The Databricks warehouse is currently **{wh['state'].lower()}**. "
                    f"{wh['message']}\n\n"
                    "Please try again in a moment -- the warehouse should be ready shortly."
                ),
                queries_executed=[],
            )
        if wh["state"] not in ("RUNNING", "FORBIDDEN") and wh["state"] not in ("NOT_CONFIGURED",):
            return ChatResponse(
                response=f"Databricks warehouse issue: {wh['message']}",
                queries_executed=[],
            )

    # Discover schema using connections and cache
    discovered = await discover_schema(ctx)

    # Merge per-request extra_context from the chat request body (if provided)
    chat_ctx = ctx
    if req.extra_context:
        chat_ctx = UserContext(
            user_id=ctx.user_id,
            extra_context=req.extra_context,
            connections=ctx.connections,
            llm_model=ctx.llm_model,
            semantic_model=ctx.semantic_model,
        )

    # Build system prompt -- all chart types available
    system = build_system_prompt(
        chat_ctx,
        discovered,
        allowed_charts=ALL_CHART_TYPES,
    )

    # -- SCHEMA-ONLY MODE: semantic model loaded but no connections --
    if not ctx.connections and ctx.semantic_model:
        logging.info(f"Schema-only mode: TMDL loaded ({len(ctx.semantic_model)} chars), no connections")
        messages = [{"role": m.role, "content": m.content} for m in req.history[-20:]]
        messages.append({"role": "user", "content": req.message})
        ai_text = await call_llm(system, messages, ctx=ctx)
        return ChatResponse(response=ai_text, queries_executed=[])

    def _clean_ai_response(text: str) -> str:
        """Strip all SQL artifacts from LLM response before returning to user."""
        # 1. Strip fenced sql_exec / sql blocks
        text = re.sub(r"```sql_exec.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```sql\b.*?```", "", text, flags=re.DOTALL)
        # 2. Strip unfenced connection= lines followed by SQL
        text = re.sub(r"^connection=\S+.*$", "", text, flags=re.MULTILINE)
        # 3. Strip standalone SQL statements (SELECT...;) not inside prose
        text = re.sub(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b[^;]*;\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
        # 4. Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # Data-only reminder injected into every user message
    DATA_FENCE = "\n\n[REMINDER: Respond ONLY with data from SQL query results. Do NOT add outside knowledge, project descriptions, strategic significance, or any information not returned by a query. Every sentence must come from a SQL result.]"

    # Build messages
    messages = [{"role": m.role, "content": m.content} for m in req.history[-20:]]
    messages.append({"role": "user", "content": req.message + DATA_FENCE})

    queries_executed = []
    max_loops = 5

    for _ in range(max_loops):
        # Call LLM
        ai_text = await call_llm(system, messages, ctx=ctx)

        # Extract sql_exec blocks (with optional connection= parameter)
        sql_blocks = re.findall(r"```sql_exec(?:\s+connection=(\S+))?\n(.*?)```", ai_text, re.DOTALL)

        if not sql_blocks:
            return ChatResponse(
                response=_clean_ai_response(ai_text), queries_executed=queries_executed,
            )

        # Execute each SQL query
        results_text = ""
        for i, (conn_id, sql) in enumerate(sql_blocks):
            sql = sql.strip()
            conn_id = conn_id.strip() if conn_id else None
            result = await execute_sql(sql, connections=ctx.connections, connection_id=conn_id)
            is_error = result.startswith("ERROR")
            queries_executed.append({"sql": sql, "result": result[:500], "error": is_error})
            results_text += f"\n\n### Query {i + 1}:\n```\n{sql}\n```\nResults:\n```\n{result}\n```"

        # Feed results back for next iteration
        messages.append({"role": "assistant", "content": ai_text})
        messages.append({
            "role": "user",
            "content": f"[SYSTEM: SQL query results]\n{results_text}\n\nPresent these results as a polished visual answer -- use charts, bold metric cards, and markdown tables. NEVER show SQL, code blocks, or describe your process. NEVER add background info, project descriptions, or outside knowledge. If the data answers the question, present it and stop. If you need more data, output another sql_exec block.",
        })

    # If we exhausted loops, return last AI response (strip SQL artifacts)
    return ChatResponse(
        response=_clean_ai_response(ai_text), queries_executed=queries_executed,
    )


@app.post("/config")
async def update_config(req: ConfigUpdate):
    """Update configuration. Does NOT handle connections."""
    config = _load_config()
    if req.llm_model is not None:
        config["llm_model"] = req.llm_model
    if req.extra_context is not None:
        config["extra_context"] = req.extra_context
    _save_config(config)
    _invalidate_all_schema()
    return {"status": "updated"}


@app.get("/config")
async def get_config():
    """Retrieve current configuration."""
    ctx = get_user_context()
    return {
        "llm_model": ctx.effective_llm_model,
        "semantic_model_loaded": bool(ctx.semantic_model),
        "semantic_model_chars": len(ctx.semantic_model),
        "extra_context": ctx.extra_context,
    }


# -- Connection CRUD endpoints --

@app.get("/connections")
async def list_connections():
    """List all configured connections (secrets redacted)."""
    ctx = get_user_context()
    result = []
    for c in ctx.connections.values():
        d = dict(c)
        # Redact secrets for GET
        if d.get("token"):
            d["token"] = d["token"][:8] + "..."
        if d.get("password"):
            d["password"] = "***"
        result.append(d)
    return {"connections": result}


@app.post("/connections")
async def save_all_connections_endpoint(req: dict):
    """Replace all connections. Detects redacted secrets and preserves originals."""
    config = _load_config()
    old_conns_list = config.get("connections", [])
    old_conns = {}
    for c in old_conns_list:
        cid = c.get("id")
        if cid:
            old_conns[cid] = c

    new_conns = req.get("connections", [])
    updated: list[dict] = []
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
        updated.append(c)

    config["connections"] = updated
    _save_config(config)
    _invalidate_all_schema()
    return {"status": "updated", "count": len(updated)}


@app.post("/test-connection/{connection_id}")
async def test_single_connection(connection_id: str):
    """Test a specific connection."""
    ctx = get_user_context()
    if connection_id not in ctx.connections:
        raise HTTPException(status_code=404, detail=f"Connection '{connection_id}' not found.")
    conn = ctx.connections[connection_id]

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
async def upload_tmdl(req: TmdlUploadRequest):
    """Accept uploaded .tmdl file contents and save as semantic model."""
    if not req.files:
        raise HTTPException(status_code=400, detail="No files provided.")

    # Filter out auto-generated / noise files
    _skip_prefixes = ("LocalDateTable_", "DateTableTemplate_")
    filtered = [
        f for f in req.files
        if not f.name.startswith("cultures/")
        and not _CULTURE_FILE_RE.match(Path(f.name).name)
        and not any(Path(f.name).stem.startswith(p) for p in _skip_prefixes)
    ]

    parts = []
    for f in sorted(filtered, key=lambda x: x.name):
        parts.append(f"=== {f.name} ===\n{f.content}")

    model_content = "\n\n".join(parts)

    # Save to local file
    _save_semantic_model(model_content)

    # If connections were provided with the upload, save them to config too
    if req.connections is not None:
        config = _load_config()
        config["connections"] = req.connections
        _save_config(config)

    _invalidate_all_schema()

    return {
        "status": "loaded",
        "files_loaded": len(filtered),
        "files_skipped": len(req.files) - len(filtered),
        "files": [f.name for f in sorted(filtered, key=lambda x: x.name)],
        "total_chars": len(model_content),
    }


@app.post("/test-connection")
async def test_connection():
    """Test the first Databricks connection -- fast check, no hanging."""
    ctx = get_user_context()
    user_conns = ctx.connections

    if not user_conns:
        return {"status": "connected", "state": "RUNNING", "message": "Backend is running."}

    # Find first Databricks connection
    conn = None
    for c in user_conns.values():
        if c.get("type") == "databricks":
            conn = c
            break
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

    # For RUNNING, FORBIDDEN, ERROR, TIMEOUT, or UNKNOWN -- try SQL directly
    if state in ("RUNNING", "FORBIDDEN", "ERROR", "TIMEOUT") or state not in _STATE_MESSAGES:
        result = await execute_sql("SELECT 1 AS test", connections=user_conns, connection_id=conn["id"])
        if result.startswith("ERROR"):
            raise HTTPException(status_code=500, detail=result)
        return {"status": "connected", "state": "RUNNING", "message": "Connected and ready."}
