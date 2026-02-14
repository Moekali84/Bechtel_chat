"""
PBIChat — Backend API
Orchestrates the agentic loop: User question → LLM → SQL → Database → LLM → Response
Deploy on Azure App Service, Azure Functions, or any Python host.
"""

import os
import re
import asyncio
from pathlib import Path
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Literal, Union
import json

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

app = FastAPI(title="PBIChat API")

# CORS — allow the Power BI visual to call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your Power BI domain
    allow_methods=["*"],
    allow_headers=["*"],
)

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
SETTINGS_PASSWORD = os.getenv("SETTINGS_PASSWORD", "Safari99")


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

class ChatResponse(BaseModel):
    response: str
    queries_executed: list[dict] = []  # {sql, result, error} for transparency

class ConfigUpdate(BaseModel):
    password: str
    openrouter_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    extra_context: Optional[str] = None

class TmdlFile(BaseModel):
    name: str
    content: str

class TmdlUploadRequest(BaseModel):
    password: str
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
async def execute_sql(sql: str, connection_id: str = None) -> str:
    """Execute SQL against a named connection (or the first available one)."""
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
        return f"ERROR: {e}"


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


def build_system_prompt(discovered_schema: str, extra_ctx: str = "") -> str:
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

**Supported chart types:** bar, line, pie, doughnut, scatter, horizontalBar

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
            err_msg = error.get("error", {}).get("message", resp.text[:200]) if isinstance(error.get("error"), dict) else str(error.get("error", resp.text[:200]))
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"OpenRouter API error: {err_msg}",
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
async def chat(req: ChatRequest):
    """Main chat endpoint — runs the agentic loop."""

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
            )
        if wh["state"] not in ("RUNNING", "FORBIDDEN") and wh["state"] not in ("NOT_CONFIGURED",):
            return ChatResponse(
                response=f"Databricks warehouse issue: {wh['message']}",
                queries_executed=[],
            )

    # Discover schema if not cached
    discovered = await discover_schema()

    # Build system prompt
    system = build_system_prompt(
        discovered,
        extra_ctx=req.extra_context,
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
            # No SQL — final response
            return ChatResponse(response=ai_text, queries_executed=queries_executed)

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

    # If we exhausted loops, return last AI response
    return ChatResponse(response=ai_text, queries_executed=queries_executed)


@app.post("/config")
async def update_config(req: ConfigUpdate):
    """Update runtime configuration (password-protected). Does NOT handle connections."""
    if req.password != SETTINGS_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password.")

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
async def get_config(password: str = Query(...)):
    """Retrieve current configuration (password-protected)."""
    if password != SETTINGS_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password.")

    return {
        "openrouter_api_key": OPENROUTER_API_KEY,
        "llm_model": LLM_MODEL,
        "semantic_model_loaded": bool(SEMANTIC_MODEL),
        "semantic_model_chars": len(SEMANTIC_MODEL),
        "extra_context": EXTRA_CONTEXT,
    }


# ── Connection CRUD endpoints ──

@app.get("/connections")
async def list_connections(password: str = Query(...)):
    """List all configured connections (secrets redacted)."""
    if password != SETTINGS_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password.")
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
async def save_all_connections_endpoint(req: dict):
    """Replace all connections (password-protected).
    Detects redacted secrets and preserves originals."""
    global CONNECTIONS, _schema_cache
    if req.get("password") != SETTINGS_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password.")

    old_conns = dict(CONNECTIONS)  # snapshot before overwriting
    new_conns = req.get("connections", [])
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
async def test_single_connection(connection_id: str):
    """Test a specific connection."""
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
async def upload_tmdl(req: TmdlUploadRequest):
    """Accept uploaded .tmdl file contents and set as semantic model."""
    if req.password != SETTINGS_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid password.")

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
