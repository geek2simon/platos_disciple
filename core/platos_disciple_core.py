# -*- coding: utf-8 -*-
import json
import time
import uuid
import io
import sys
import traceback
import re
import getpass
import socket
from datetime import datetime
import psycopg
from psycopg.rows import dict_row
from openai import OpenAI

import os
from pathlib import Path

# =====================================================
# Configuration module
# =====================================================

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
CONFIG_FILE = PROJECT_ROOT / "config" / "config_general.py"

# Project layout:
#   platos_disciple/
#   |-- core/platos_disciple_core.py
#   `-- config/config_general.py
#
# Add the project root explicitly so this import works regardless of the
# current working directory on both Windows and Linux.
project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

try:
    from config.config_general import (
        API_KEY,
        DB_HOST,
        DB_NAME,
        DB_PASSWORD,
        DB_PORT,
        DB_USER,
        DEPLOYMENT_NAME,
        ENDPOINT_URL,
    )
except ImportError as exc:
    raise RuntimeError(
        f"Cannot load configuration from: {CONFIG_FILE}"
    ) from exc


# =====================================================
# Config
# =====================================================

DB_CONFIG = {
    "host": DB_HOST,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "dbname": DB_NAME,
    "port": int(DB_PORT),
}

endpoint = ENDPOINT_URL
deployment_name = DEPLOYMENT_NAME
api_key = API_KEY

client = OpenAI(
    base_url=endpoint,
    api_key=api_key
)


# =====================================================
# Thread state
# =====================================================

THREAD = {
    "id": str(uuid.uuid4()),
    "title": "New Topic",
    "summary": "",
    "history": [],
    "docs": [],
    "last_search_query": "",
    "retrieval_stats": {},
    "current_doc_id": None,
    "current_file_name": "",
    "current_full_path": "",
    "current_document_mode": False,
    "last_rag_scope": {},
    "last_rag_scope_mode": "AUTO",
    "last_semantic_query": "",
}



# =====================================================
# Unified execution observability
# =====================================================

CURRENT_EXECUTION = None

# =====================================================
# Structured GUI presentation state
# =====================================================
# Core owns search/retrieval meaning. GUI only renders this structure.
LAST_GUI_RESULT = {
    "highlight_terms": [],
    "retrieved_documents": [],
}


def reset_gui_result():
    global LAST_GUI_RESULT
    LAST_GUI_RESULT = {
        "highlight_terms": [],
        "retrieved_documents": [],
    }


def set_gui_highlight_terms(terms):
    """Expose actual core search/expanded terms to the GUI."""
    global LAST_GUI_RESULT

    cleaned = []
    seen = set()

    for term in terms or []:
        value = str(term or "").strip()
        if not value:
            continue

        key = value.lower()
        if key in seen:
            continue

        seen.add(key)
        cleaned.append(value)

    LAST_GUI_RESULT["highlight_terms"] = cleaned


def set_gui_retrieved_documents(docs):
    """Expose retrieved document names/paths without changing retrieval logic."""
    global LAST_GUI_RESULT

    result = []
    seen = set()

    for d in docs or []:
        path = str(d.get("FullPath") or "").strip()
        name = str(d.get("FileName") or "").strip()

        key = (path or name).lower()
        if not key or key in seen:
            continue

        seen.add(key)
        result.append({
            "DocID": d.get("DocID"),
            "FileName": name,
            "FullPath": path,
            "RankingScore": d.get("RankingScore", d.get("BestScore")),
            "BestScore": d.get("BestScore"),
        })

    LAST_GUI_RESULT["retrieved_documents"] = result


def get_gui_result():
    """Return a safe copy of the current structured GUI presentation result."""
    return {
        "highlight_terms": list(LAST_GUI_RESULT.get("highlight_terms") or []),
        "retrieved_documents": [
            dict(x)
            for x in (LAST_GUI_RESULT.get("retrieved_documents") or [])
        ],
    }


def _json_safe(value):
    """Convert Python values into JSON-safe values for logging."""
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def get_caller_identity():
    """Return the current Windows login and the calling machine name + IP."""
    username = os.getenv("USERNAME") or getpass.getuser() or "unknown"
    user_domain = os.getenv("USERDOMAIN")
    if user_domain and "\\" not in username:
        username = f"{user_domain}\\{username}"

    hostname = socket.gethostname() or "unknown"
    try:
        ip_addresses = sorted({
            ip
            for ip in socket.gethostbyname_ex(hostname)[2]
            if ip and not ip.startswith("127.")
        })
    except OSError:
        ip_addresses = []

    calling_from = (
        f"{hostname} ({', '.join(ip_addresses)})"
        if ip_addresses
        else hostname
    )
    return username, calling_from


def begin_execution(user_question, user_command=None):
    """Start one AgentExecutionLog trace for one user request."""
    global CURRENT_EXECUTION
    username, calling_from = get_caller_identity()
    CURRENT_EXECUTION = {
        "ExecutionID": str(uuid.uuid4()),
        "ThreadID": THREAD["id"],
        "RequestStartedAt": datetime.now(),
        "UserQuestion": user_question,
        "UserCommand": user_command,
        "Username": username,
        "CallingFrom": calling_from,
        "RouterIntent": None,
        "RouterJSON": None,
        "RouterSearchQuery": None,
        "RouterTopicTitle": None,
        "ForceNewSearch": None,
        "SearchMode": None,
        "PresentationMode": None,
        "SQLTrace": [],
        "RowsReturned": None,
        "SearchQuery": None,
        "NewSearch": None,
        "RetrievalStats": {},
        "DocumentsUsed": [],
        "ChunksUsed": [],
        "GPTCalls": [],
        "FinalAnswer": None,
        "ExecutionStatus": "Success",
        "ErrorStage": None,
        "ErrorType": None,
        "ErrorMessage": None,
        "ErrorTraceback": None,
    }
    return CURRENT_EXECUTION


def set_execution_mode(search_mode=None, presentation_mode=None):
    if CURRENT_EXECUTION is None:
        return
    if search_mode is not None:
        CURRENT_EXECUTION["SearchMode"] = search_mode
    if presentation_mode is not None:
        CURRENT_EXECUTION["PresentationMode"] = presentation_mode


def trace_sql(name, sql, params, rows_returned, started_at, completed_at, elapsed_ms):
    """Add one executed SQL statement to the current trace."""
    if CURRENT_EXECUTION is None:
        return
    CURRENT_EXECUTION["SQLTrace"].append({
        "name": name,
        "sql": sql.strip(),
        "parameters": list(params or []),
        "rows_returned": rows_returned,
        "started_at": started_at.isoformat(sep=" "),
        "completed_at": completed_at.isoformat(sep=" "),
        "elapsed_ms": elapsed_ms,
    })
    CURRENT_EXECUTION["RowsReturned"] = rows_returned


class CaseInsensitiveDict(dict):
    """
    Dictionary with case-insensitive string-key lookup.

    PostgreSQL returns unquoted column names in lowercase (docid, filename, ...),
    while the existing agent code uses MySQL-style keys (DocID, FileName, ...).
    This wrapper lets the rest of the mature RAG code stay unchanged.
    """
    def _resolve_key(self, key):
        if not isinstance(key, str):
            return key
        if dict.__contains__(self, key):
            return key
        target = key.lower()
        for existing_key in dict.keys(self):
            if isinstance(existing_key, str) and existing_key.lower() == target:
                return existing_key
        return key

    def __getitem__(self, key):
        return dict.__getitem__(self, self._resolve_key(key))

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if dict.__contains__(self, key):
            return True
        if isinstance(key, str):
            target = key.lower()
            return any(
                isinstance(existing_key, str) and existing_key.lower() == target
                for existing_key in dict.keys(self)
            )
        return False


def _wrap_pg_row(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return CaseInsensitiveDict(row)
    return row


def execute_select(conn, sql, params=(), fetch="all", trace_name="SQL"):
    """Execute a SELECT and record SQL text, parameters, row count and timing."""
    started_at = datetime.now()
    started_perf = time.perf_counter()
    cur = conn.cursor(row_factory=dict_row)
    try:
        cur.execute(sql, tuple(params or ()))
        if fetch == "one":
            result = _wrap_pg_row(cur.fetchone())
            rows_returned = 1 if result is not None else 0
        else:
            result = [_wrap_pg_row(row) for row in cur.fetchall()]
            rows_returned = len(result)
        return result
    finally:
        completed_at = datetime.now()
        elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
        trace_sql(
            trace_name,
            sql,
            params,
            locals().get("rows_returned"),
            started_at,
            completed_at,
            elapsed_ms,
        )
        cur.close()


class TeeOutput(io.StringIO):
    """Capture console output while continuing to display it."""
    def __init__(self, original):
        super().__init__()
        self.original = original

    def write(self, value):
        self.original.write(value)
        return super().write(value)

    def flush(self):
        self.original.flush()
        return super().flush()


# =====================================================
# Minimal console highlighting
# =====================================================

ANSI_YELLOW = "\033[43m\033[30m\033[1m"
ANSI_RESET = "\033[0m"


def console_highlight(text, terms):
    """
    Standalone console presentation only.
    Core decides which terms matter; this function only colors them.
    """
    result = str(text or "")

    for term in sorted(
        {str(x).strip() for x in (terms or []) if str(x).strip()},
        key=len,
        reverse=True,
    ):
        result = re.sub(
            re.escape(term),
            lambda m: f"{ANSI_YELLOW}{m.group(0)}{ANSI_RESET}",
            result,
            flags=re.IGNORECASE,
        )

    return result


def build_rag_log_details(docs, max_chunks_per_doc=3):
    documents_used = []
    chunks_used = []

    for d in docs or []:
        documents_used.append({
            "ThreadID": THREAD["id"],
            "DocID": d["DocID"],
            "FileName": d["FileName"],
            "FullPath": d["FullPath"],
            "BestScore": d["BestScore"],
            "RankingScore": d.get("RankingScore", d["BestScore"]),
            "Top3AverageScore": d.get("Top3AverageScore", d["BestScore"]),
            "ChunkCountInRetrievedSet": len(d.get("Chunks", [])),
        })

        for c in d.get("Chunks", [])[:max_chunks_per_doc]:
            chunks_used.append({
                "ChunkID": c["ChunkID"],
                "DocID": c["DocID"],
                "FileName": c["FileName"],
                "ChunkNo": c["ChunkNo"],
                "Score": float(c["Score"] or 0),
            })

    return documents_used, chunks_used


def write_execution_log(conn):
    """Write one completed request into AgentExecutionLog."""
    if CURRENT_EXECUTION is None:
        return

    e = CURRENT_EXECUTION
    completed_at = datetime.now()
    elapsed_ms = int((completed_at - e["RequestStartedAt"]).total_seconds() * 1000)

    sql_trace = e.get("SQLTrace") or []
    main_sql = sql_trace[-1] if sql_trace else {}
    first_sql = sql_trace[0] if sql_trace else {}
    total_sql_ms = sum(int(x.get("elapsed_ms") or 0) for x in sql_trace)

    gpt_calls = e.get("GPTCalls") or []
    prompt_tokens = sum(int(x.get("prompt_tokens") or 0) for x in gpt_calls) or None
    completion_tokens = sum(int(x.get("completion_tokens") or 0) for x in gpt_calls) or None
    total_tokens = sum(int(x.get("total_tokens") or 0) for x in gpt_calls) or None
    total_gpt_ms = sum(int(x.get("elapsed_ms") or 0) for x in gpt_calls)
    prompt_chars = sum(int(x.get("prompt_chars") or 0) for x in gpt_calls) or None

    retrieval = e.get("RetrievalStats") or {}
    answer = e.get("FinalAnswer") or ""

    insert_sql = """
    INSERT INTO AgentExecutionLog
    (
        ExecutionID, ThreadID,
        RequestStartedAt, RequestCompletedAt,
        UserQuestion, UserCommand, username, callingfrom,
        RouterIntent, RouterJSON, RouterSearchQuery, RouterTopicTitle, ForceNewSearch,
        SearchMode, PresentationMode,
        GeneratedSQL, SQLParameters, RowsReturned,
        SQLStartedAt, SQLCompletedAt, SQLElapsedMS, SQLTraceJSON,
        SearchQuery, NewSearch,
        MatchedChunks, ChunkLimit, ChunksRetrieved, UniqueDocumentsMatched,
        DocumentsSent, ChunksSentToGPT, MaxChunksPerDoc, MaxCharsPerChunk,
        DocumentRankingMethod, RetrievalStatsJSON,
        DocumentsUsed, ChunksUsed,
        GPTUsed, GPTModel, GPTPurpose,
        PromptChars, PromptTokens, CompletionTokens, TotalTokens,
        GPTStartedAt, GPTCompletedAt, GPTElapsedMS, GPTUsageJSON,
        PromptSample,
        FinalAnswer, AnswerChars,
        ExecutionStatus, ElapsedMS,
        ErrorStage, ErrorType, ErrorMessage, ErrorTraceback
    )
    VALUES
    (
        %s,%s,
        %s,%s,
        %s,%s,%s,%s,
        %s,%s,%s,%s,%s,
        %s,%s,
        %s,%s,%s,
        %s,%s,%s,%s,
        %s,%s,
        %s,%s,%s,%s,
        %s,%s,%s,%s,
        %s,%s,
        %s,%s,
        %s,%s,%s,
        %s,%s,%s,%s,
        %s,%s,%s,%s,
        %s,
        %s,%s,
        %s,%s,
        %s,%s,%s,%s
    );
    """

    params = (
        e["ExecutionID"], e.get("ThreadID"),
        e["RequestStartedAt"], completed_at,
        e.get("UserQuestion"), e.get("UserCommand"),
        e.get("Username"), e.get("CallingFrom"),
        e.get("RouterIntent"),
        json.dumps(e.get("RouterJSON"), ensure_ascii=False, default=_json_safe) if e.get("RouterJSON") is not None else None,
        e.get("RouterSearchQuery"), e.get("RouterTopicTitle"),
        1 if e.get("ForceNewSearch") else 0 if e.get("ForceNewSearch") is not None else None,
        e.get("SearchMode"), e.get("PresentationMode"),
        main_sql.get("sql"),
        json.dumps(main_sql.get("parameters"), ensure_ascii=False, default=_json_safe) if main_sql else None,
        e.get("RowsReturned"),
        first_sql.get("started_at"), main_sql.get("completed_at"), total_sql_ms if sql_trace else None,
        json.dumps(sql_trace, ensure_ascii=False, default=_json_safe) if sql_trace else None,
        e.get("SearchQuery"),
        1 if e.get("NewSearch") else 0 if e.get("NewSearch") is not None else None,
        retrieval.get("MatchedChunks"), retrieval.get("ChunkLimit"),
        retrieval.get("ChunksRetrieved"), retrieval.get("UniqueDocumentsMatched"),
        retrieval.get("DocumentsSent", len(e.get("DocumentsUsed") or [])),
        len(e.get("ChunksUsed") or []),
        retrieval.get("MaxChunksPerDoc", 3 if e.get("DocumentsUsed") else None),
        retrieval.get("MaxCharsPerChunk", 1600 if e.get("DocumentsUsed") else None),
        retrieval.get("DocumentRankingMethod"),
        json.dumps(retrieval, ensure_ascii=False, default=_json_safe) if retrieval else None,
        json.dumps(e.get("DocumentsUsed"), ensure_ascii=False, default=_json_safe) if e.get("DocumentsUsed") else None,
        json.dumps(e.get("ChunksUsed"), ensure_ascii=False, default=_json_safe) if e.get("ChunksUsed") else None,
        1 if gpt_calls else 0,
        deployment_name if gpt_calls else None,
        "MULTIPLE" if len(gpt_calls) > 1 else (gpt_calls[0].get("purpose") if gpt_calls else None),
        prompt_chars, prompt_tokens, completion_tokens, total_tokens,
        gpt_calls[0].get("started_at") if gpt_calls else None,
        gpt_calls[-1].get("completed_at") if gpt_calls else None,
        total_gpt_ms if gpt_calls else None,
        json.dumps(gpt_calls, ensure_ascii=False, default=_json_safe) if gpt_calls else None,
        None,
        answer, len(answer),
        e.get("ExecutionStatus", "Success"), elapsed_ms,
        e.get("ErrorStage"), e.get("ErrorType"),
        e.get("ErrorMessage"), e.get("ErrorTraceback"),
    )

    cur = conn.cursor()
    try:
        cur.execute(insert_sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        print("\n[Warning] Failed to write AgentExecutionLog:")
        print(traceback.format_exc())
    finally:
        cur.close()



# =====================================================
# OpenAI helpers
# =====================================================

def gpt_call(messages, temperature=0.2, purpose="GENERAL"):
    """Return text/token usage and add this model call to the current trace."""
    started_at = datetime.now()
    started_perf = time.perf_counter()

    resp = client.chat.completions.create(
        model=deployment_name,
        messages=messages,
        temperature=temperature,
    )

    completed_at = datetime.now()
    elapsed_ms = int((time.perf_counter() - started_perf) * 1000)

    usage = getattr(resp, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    total_tokens = getattr(usage, "total_tokens", None) if usage else None
    prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)

    if CURRENT_EXECUTION is not None:
        CURRENT_EXECUTION["GPTCalls"].append({
            "purpose": purpose,
            "started_at": started_at.isoformat(sep=" "),
            "completed_at": completed_at.isoformat(sep=" "),
            "elapsed_ms": elapsed_ms,
            "prompt_chars": prompt_chars,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        })

    return {
        "text": resp.choices[0].message.content,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def gpt_text(messages, temperature=0.2):
    return gpt_call(messages, temperature)["text"]


def gpt_json(messages, temperature=0.1):
    text = gpt_text(messages, temperature)

    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


# =====================================================
# Settings
# =====================================================

def get_int_setting(conn, name, default_value):
    row = execute_select(
        conn,
        "SELECT SettingValue FROM AgentSetting WHERE SettingName=%s",
        (name,),
        fetch="one",
        trace_name="AgentSetting"
    )

    if not row:
        return default_value

    try:
        return int(row["SettingValue"])
    except Exception:
        return default_value


# =====================================================
# Router
# =====================================================

def route_question(user_question):
    prompt = f"""
You are an intent router for an enterprise document knowledge agent.

Return JSON only.

Available intents:

1. FILE_COUNT
For questions asking total number of files/documents.
Examples:
- How many files do I have?
- Total indexed files?
- 我一共有多少文件？

2. TYPE_COUNT
For file type statistics.
Examples:
- How many PDFs?
- Count by extension.
- PDF Excel Word 各有多少？

3. FOLDER_CATALOG
For folder/category structure.
Examples:
- Show folder structure.
- Organize by folder.
- 文件夹结构是什么？

4. FILE_LIST
For listing files by metadata only: file name, path, folder, extension, modified time.
Use this only when the user is asking about file metadata, not document body content.
Examples:
- List all PDF files.
- Show files whose filename contains coffee.
- 列出文件名包含 coffee 的文件。
- 列出所有 mhtml 文件。

5. SEARCH_ONLY
For raw keyword search, no GPT summary.

6. RAG_ANSWER
For normal document Q&A.

7. CATALOG
For index/catalog organized by category, year, company, subject, document type.

8. HELP
For help.

9. NEW_THREAD
For starting a new topic.

10. THREAD_STATUS
For showing current thread.

11. SINGLE_DOCUMENT_ANALYSIS
For analyzing, summarizing, or asking questions about one specific file/article/document.
Use this when the user names a specific file, especially with an extension such as .mhtml, .mht, .pdf, .docx, .xlsx, .pptx, or asks to analyze "this article", "this file", "the above document", "此文章", "这个文件", "继续分析" while a current document is active.

Return this JSON:
{{
  "intent": "...",
  "search_query": "...",
  "topic_title": "...",
  "force_new_search": true or false
}}

User question:
{user_question}

Current thread summary:
{THREAD["summary"]}
"""

    data = gpt_json([
        {"role": "system", "content": "You are a strict JSON intent router."},
        {"role": "user", "content": prompt},
    ])

    route = {
        "intent": data.get("intent", "RAG_ANSWER"),
        "search_query": data.get("search_query", user_question),
        "topic_title": data.get("topic_title", "Topic"),
        "force_new_search": bool(data.get("force_new_search", False)),
    }

    if CURRENT_EXECUTION is not None:
        CURRENT_EXECUTION["RouterIntent"] = route["intent"]
        CURRENT_EXECUTION["RouterJSON"] = route.copy()
        CURRENT_EXECUTION["RouterSearchQuery"] = route["search_query"]
        CURRENT_EXECUTION["RouterTopicTitle"] = route["topic_title"]
        CURRENT_EXECUTION["ForceNewSearch"] = route["force_new_search"]

    return route


def rewrite_search_query(user_question):
    prompt = f"""
Convert the user's question into PostgreSQL bilingual text-search keywords.

Rules:
1. Preserve important original terms.
2. If the question is Chinese, add English equivalents.
3. Add business synonyms.
4. Do not answer the question.
5. Return JSON only.

JSON:
{{
  "search_query": "...",
  "topic_title": "..."
}}

User question:
{user_question}

Current thread summary:
{THREAD["summary"]}
"""

    data = gpt_json([
        {"role": "system", "content": "You rewrite user questions into search keywords. Return JSON only."},
        {"role": "user", "content": prompt},
    ])

    return {
        "search_query": data.get("search_query", user_question),
        "topic_title": data.get("topic_title", "Topic"),
    }


# =====================================================
# Metadata SQL
# =====================================================

def get_file_count(conn):
    sql = """
    SELECT
        COUNT(*) AS TotalFiles,
        SUM(CASE WHEN ParseStatus='Success' THEN 1 ELSE 0 END) AS ParsedSuccess,
        SUM(CASE WHEN ParseStatus='Failed' THEN 1 ELSE 0 END) AS ParsedFailed,
        SUM(CASE WHEN ParseStatus='Pending' THEN 1 ELSE 0 END) AS Pending,
        ROUND(SUM(FileSizeBytes)::NUMERIC / 1024 / 1024, 2) AS TotalSizeMB
    FROM Document;
    """
    return execute_select(conn, sql, fetch="one", trace_name="FileCount")


def get_type_count(conn):
    sql = """
    SELECT
        Extension,
        ParseStatus,
        COUNT(*) AS FileCount,
        ROUND(SUM(FileSizeBytes)::NUMERIC / 1024 / 1024, 2) AS TotalSizeMB
    FROM Document
    GROUP BY Extension, ParseStatus
    ORDER BY FileCount DESC;
    """
    return execute_select(conn, sql, trace_name="TypeCount")


def get_folder_catalog(conn, limit=500):
    sql = """
    SELECT
        FolderLevel1,
        FolderLevel2,
        FolderLevel3,
        Extension,
        COUNT(*) AS FileCount
    FROM Document
    GROUP BY FolderLevel1, FolderLevel2, FolderLevel3, Extension
    ORDER BY FolderLevel1, FolderLevel2, FolderLevel3, FileCount DESC
    LIMIT %s;
    """
    return execute_select(conn, sql, (limit,), trace_name="FolderCatalog")


def list_files(conn, keyword=None, limit=500):
    params = []
    where = ""

    if keyword:
        where = """
        WHERE FileName ILIKE %s
           OR FullPath ILIKE %s
           OR FolderPath ILIKE %s
           OR Extension ILIKE %s
        """
        like = f"%{keyword}%"
        params = [like, like, like, like]

    sql = f"""
    SELECT
        DocID,
        FileName,
        Extension,
        FullPath,
        ModifiedTime,
        ParseStatus
    FROM Document
    {where}
    ORDER BY FullPath
    LIMIT %s;
    """

    params.append(limit)

    return execute_select(conn, sql, tuple(params), trace_name="FileList")





def resolve_folder_level1(conn, text):
    """
    Resolve a user-mentioned top-level category against Document.FolderLevel1.

    Minimal behavior:
    1. exact match, case-insensitive
    2. category contained in the user/router text
    3. user/router text contained in the category

    Returns the canonical FolderLevel1 value or None.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    rows = execute_select(
        conn,
        """
        SELECT DISTINCT FolderLevel1
        FROM Document
        WHERE FolderLevel1 IS NOT NULL
          AND TRIM(FolderLevel1) <> ''
        ORDER BY LENGTH(FolderLevel1) DESC, FolderLevel1;
        """,
        trace_name="ResolveFolderLevel1"
    )

    q = raw.lower()

    # Exact match first.
    for row in rows:
        category = str(row.get("FolderLevel1") or "").strip()
        if category and category.lower() == q:
            return category

    # Then resolve category names embedded in natural-language questions,
    # e.g. "What files are in the 2025 projects? Give me a list."
    for row in rows:
        category = str(row.get("FolderLevel1") or "").strip()
        if category and category.lower() in q:
            return category

    # Finally allow the router text to be a shortened category phrase.
    for row in rows:
        category = str(row.get("FolderLevel1") or "").strip()
        if q and q in category.lower():
            return category

    return None


def list_files_by_folder_level1(conn, folder_level1, limit=500):
    """List files in one resolved top-level category."""
    sql = """
    SELECT
        DocID,
        FileName,
        Extension,
        FullPath,
        ModifiedTime,
        ParseStatus
    FROM Document
    WHERE FolderLevel1 = %s
    ORDER BY FullPath
    LIMIT %s;
    """
    return execute_select(
        conn,
        sql,
        (folder_level1, limit),
        trace_name="FileListByFolderLevel1"
    )

def get_latest_modified_files(conn, limit=1):
    """
    Return the most recently modified files based on Document.ModifiedTime.

    This is for questions such as:
    - 最新更新的文件是哪个？
    - 最近修改的文件有哪些？
    - What is the most recently modified file?

    The SQL is executed through execute_select(), so it is automatically
    captured in AgentExecutionLog.GeneratedSQL / SQLTraceJSON.
    """
    sql = """
    SELECT
        DocID,
        FileName,
        Extension,
        FullPath,
        ModifiedTime,
        ParseStatus
    FROM Document
    WHERE ModifiedTime IS NOT NULL
    ORDER BY ModifiedTime DESC, DocID DESC
    LIMIT %s;
    """

    return execute_select(
        conn,
        sql,
        (limit,),
        trace_name="LatestModifiedFiles"
    )


def is_latest_modified_question(user_question):
    """Detect metadata questions that ask for the latest modified/updated file."""
    q = (user_question or "").strip().lower()

    phrases = (
        "最新更新",
        "最近更新",
        "最后更新",
        "最新修改",
        "最近修改",
        "最后修改",
        "最新改动",
        "最近改动",
        "latest updated",
        "latest update",
        "last updated",
        "most recently updated",
        "latest modified",
        "last modified",
        "most recently modified",
        "newest modified",
    )

    return any(phrase in q for phrase in phrases)


def browse_summaries(conn, keyword=None, limit=100):
    params = []
    where = ""

    if keyword:
        where = """
        WHERE d.FileName ILIKE %s
           OR d.FullPath ILIKE %s
           OR s.Title ILIKE %s
           OR s.DocType ILIKE %s
           OR s.Summary ILIKE %s
           OR s.Keywords ILIKE %s
           OR s.Topics ILIKE %s
           OR s.Companies ILIKE %s
           OR s.Products ILIKE %s
           OR s.Countries ILIKE %s
        """
        like = f"%{keyword}%"
        params = [like] * 10

    sql = f"""
    SELECT
        d.DocID,
        d.FileName,
        d.Extension,
        d.FullPath,
        s.Title,
        s.DocType,
        s.Keywords,
        s.Topics,
        s.Companies,
        s.Products,
        s.Countries,
        s.Years,
        s.ImportanceScore,
        LEFT(s.Summary, 500) AS SummarySample
    FROM Document d
    LEFT JOIN DocumentSummary s ON d.DocID = s.DocID
    {where}
    ORDER BY s.ImportanceScore DESC, d.ModifiedTime DESC
    LIMIT %s;
    """

    params.append(limit)

    return execute_select(conn, sql, tuple(params), trace_name="BrowseSummaries")




def get_category_stats(conn, level=1, limit=100):
    """Return compact folder/category statistics. This is safe to send to GPT."""
    if level == 3:
        select_cols = "FolderLevel1, FolderLevel2, FolderLevel3"
        group_cols = "FolderLevel1, FolderLevel2, FolderLevel3"
        order_cols = "FileCount DESC, FolderLevel1, FolderLevel2, FolderLevel3"
    elif level == 2:
        select_cols = "FolderLevel1, FolderLevel2"
        group_cols = "FolderLevel1, FolderLevel2"
        order_cols = "FileCount DESC, FolderLevel1, FolderLevel2"
    else:
        select_cols = "FolderLevel1"
        group_cols = "FolderLevel1"
        order_cols = "FileCount DESC, FolderLevel1"

    sql = f"""
    SELECT
        {select_cols},
        COUNT(*) AS FileCount,
        ROUND(SUM(FileSizeBytes)::NUMERIC / 1024 / 1024, 2) AS TotalSizeMB,
        SUM(CASE WHEN ParseStatus='Success' THEN 1 ELSE 0 END) AS ParsedSuccess,
        SUM(CASE WHEN ParseStatus='Failed' THEN 1 ELSE 0 END) AS ParsedFailed
    FROM Document
    GROUP BY {group_cols}
    ORDER BY {order_cols}
    LIMIT %s;
    """

    return execute_select(conn, sql, (limit,), trace_name="CategoryStats")


def get_catalog_summary_for_gpt(conn):
    """Small multi-section catalogue summary, not full 1810 metadata rows."""
    return {
        "file_count": get_file_count(conn),
        "category_stats": get_category_stats(conn, level=1, limit=50),
        "type_stats": get_type_count(conn),
    }


# =====================================================
# Single-document conversation mode
# =====================================================

DOCUMENT_EXTENSIONS = (
    ".mhtml", ".mht", ".pdf", ".docx", ".doc",
    ".xlsx", ".xls", ".pptx", ".ppt",
    ".txt", ".html", ".htm"
)


def _clean_extracted_filename(value):
    """Normalize a filename extracted from a natural-language request."""
    if not value:
        return None

    name = str(value).strip()

    # Remove surrounding Chinese/English quotation marks and brackets.
    name = name.strip(" \t\r\n\"'“”‘’《》〈〉【】[]()（）")

    # Remove natural-language prefixes repeatedly.
    prefix_pattern = re.compile(
        r"^(?:请\s*)?(?:帮我\s*)?(?:阅读|分析|总结|概括|查看|打开|研究|解读|浏览)"
        r"(?:一下|下)?(?:这篇|该篇|这个|该|此)?(?:文章|文件|文档|报告|网页)?"
        r"(?:名为|叫做|名称为)?\s*[:：,，-]?\s*",
        flags=re.I,
    )

    previous = None
    while name and name != previous:
        previous = name
        name = prefix_pattern.sub("", name).strip()
        name = name.strip(" \t\r\n\"'“”‘’《》〈〉【】[]()（）")

    # Remove common trailing request text accidentally captured after the extension.
    ext_pattern = "|".join(
        sorted((re.escape(ext) for ext in DOCUMENT_EXTENSIONS), key=len, reverse=True)
    )
    m = re.search(rf"(?is)^(.+?(?:{ext_pattern}))(?=$|[》〉】\]）)\s,，。；;:：!?！？])", name)
    if m:
        name = m.group(1).strip()

    return Path(name).name.strip() or None


def extract_named_filename(user_question):
    """
    Extract a specific filename from natural language without using GPT.

    Supported examples:
    - 请阅读文件《abc.mhtml》，总结主要内容。
    - 分析“abc.pdf”
    - 打开 abc.docx 并概括
    - abc.xlsx
    """
    q = (user_question or "").strip()
    if not q:
        return None

    ext_pattern = "|".join(
        sorted((re.escape(ext) for ext in DOCUMENT_EXTENSIONS), key=len, reverse=True)
    )

    # 1. Highest-confidence form: filename inside Chinese book-title brackets.
    bracket_patterns = (
        rf"《\s*([^《》\r\n]+?(?:{ext_pattern}))\s*》",
        rf"〈\s*([^〈〉\r\n]+?(?:{ext_pattern}))\s*〉",
        rf"【\s*([^【】\r\n]+?(?:{ext_pattern}))\s*】",
    )
    for pattern in bracket_patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_extracted_filename(m.group(1))

    # 2. Quoted filename.
    quoted_patterns = (
        rf'["“]([^"”\r\n]+?(?:{ext_pattern}))["”]',
        rf"['‘]([^'’\r\n]+?(?:{ext_pattern}))['’]",
    )
    for pattern in quoted_patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_extracted_filename(m.group(1))

    # 3. Unquoted filename. Stop at punctuation or common instruction words after extension.
    m = re.search(
        rf"([^\r\n<>《》〈〉【】\"“”'‘’]+?(?:{ext_pattern}))"
        rf"(?=$|[》〉】\]）)\s,，。；;:：!?！？])",
        q,
        flags=re.I,
    )
    if m:
        return _clean_extracted_filename(m.group(1))

    return None

def extract_filename_stem_candidate(user_question):
    """
    Extract a likely document title even when the user omits the extension.

    Examples:
    - 分析《2026年行业发展报告》
    - 请阅读文件 2026年行业发展报告

    This helper is intentionally conservative and only runs for clear
    single-document verbs/markers.
    """
    q = (user_question or "").strip()
    if not q:
        return None

    single_doc_markers = (
        "阅读", "分析", "总结", "概括", "解读", "查看", "打开",
        "这篇文章", "该文章", "此文章", "这个文件", "该文件", "此文件",
        "this article", "this file", "this document", "summarize", "analyze", "read"
    )
    qlower = q.lower()
    if not any(m in qlower for m in single_doc_markers):
        return None

    # Prefer content inside Chinese/English title brackets or quotes.
    patterns = (
        r"《\s*([^《》\r\n]{4,300})\s*》",
        r"〈\s*([^〈〉\r\n]{4,300})\s*〉",
        r"【\s*([^【】\r\n]{4,300})\s*】",
        r'["“]([^"”\r\n]{4,300})["”]',
        r"['‘]([^'’\r\n]{4,300})['’]",
    )
    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            candidate = _clean_extracted_filename(m.group(1))
            return candidate

    # Fallback: remove a leading instruction and common trailing request text.
    candidate = re.sub(
        r"^(?:请\s*)?(?:帮我\s*)?(?:阅读|分析|总结|概括|查看|打开|研究|解读|浏览)"
        r"(?:一下|下)?(?:这篇|该篇|这个|该|此)?(?:文章|文件|文档|报告|网页)?"
        r"(?:名为|叫做|名称为)?\s*[:：,，-]?\s*",
        "",
        q,
        flags=re.I,
    ).strip()

    candidate = re.split(
        r"(?:，|,|。|；|;|\n|\r|\s)+(?:请|并|然后)?(?:总结|分析|概括|说明|给出|列出|介绍|解读)",
        candidate,
        maxsplit=1,
        flags=re.I,
    )[0].strip()

    candidate = candidate.strip(" \t\r\n\"'“”‘’《》〈〉【】[]()（）")
    return Path(candidate).name.strip() if len(candidate) >= 4 else None


def is_document_followup(user_question):
    q = (user_question or "").strip().lower()
    markers = (
        "此文章", "这篇文章", "该文章", "此文件", "这个文件", "该文件",
        "此文档", "这个文档", "该文档", "上面文章", "上述文章", "继续",
        "详细一点", "进一步分析", "分析其内容", "分析此文章内容",
        "this article", "this file", "this document", "the article", "the file",
        "continue", "more detail", "analyze it", "summarize it"
    )
    return any(x in q for x in markers)

def find_document_by_filename(conn, filename):
    """
    Resolve one document from its filename or title.

    Priority:
    1. Exact FileName match
    2. Exact filename stem match (extension omitted)
    3. FileName starts with title
    4. FileName / FullPath contains title

    The caller must still handle ambiguity instead of silently picking an
    unrelated document.
    """
    if not filename:
        return []

    base = Path(filename).name.strip()
    stem = Path(base).stem if Path(base).suffix else base

    sql = """
    SELECT
        DocID, FileName, Extension, FullPath, ModifiedTime, ParseStatus,
        CASE
            WHEN FileName = %s THEN 0
            WHEN LOWER(REGEXP_REPLACE(FileName, '\\.[^.]+$', '')) = LOWER(%s) THEN 1
            WHEN FileName ILIKE %s THEN 2
            WHEN FileName ILIKE %s THEN 3
            WHEN FullPath ILIKE %s THEN 4
            ELSE 9
        END AS MatchRank
    FROM Document
    WHERE FileName = %s
       OR LOWER(REGEXP_REPLACE(FileName, '\\.[^.]+$', '')) = LOWER(%s)
       OR FileName ILIKE %s
       OR FileName ILIKE %s
       OR FullPath ILIKE %s
    ORDER BY MatchRank, LENGTH(FileName), ModifiedTime DESC, DocID DESC
    LIMIT 20;
    """

    params = (
        base, stem, f"{stem}.%", f"%{base}%", f"%{base}%",
        base, stem, f"{stem}.%", f"%{base}%", f"%{base}%",
    )
    return execute_select(conn, sql, params, trace_name="FindSingleDocument")

def set_current_document(row):
    THREAD["current_doc_id"] = row.get("DocID")
    THREAD["current_file_name"] = row.get("FileName") or ""
    THREAD["current_full_path"] = row.get("FullPath") or ""
    THREAD["current_document_mode"] = True
    THREAD["title"] = row.get("FileName") or THREAD.get("title", "Document")
    THREAD["docs"] = []
    THREAD["last_search_query"] = f"DocID={row.get('DocID')}"

def get_document_chunks(conn, doc_id, max_chars=3000, chunk_limit=80):
    sql = """
    SELECT
        c.ChunkID, d.DocID, d.FileName, d.FullPath, d.Extension,
        d.FolderLevel1, d.FolderLevel2, d.FolderLevel3,
        c.ChunkNo, c.ChunkSource, c.PageNo, c.SheetName,
        LEFT(c.ChunkText, %s) AS TextSample,
        1.0 AS Score
    FROM DocumentChunk c
    JOIN Document d ON d.DocID = c.DocID
    WHERE c.DocID = %s
    ORDER BY c.ChunkNo, c.ChunkID
    LIMIT %s;
    """
    return execute_select(conn, sql, (max_chars, doc_id, chunk_limit), trace_name="SingleDocumentChunks")

def count_document_chunks(conn, doc_id):
    row = execute_select(
        conn,
        "SELECT COUNT(*) AS ChunkCount FROM DocumentChunk WHERE DocID=%s",
        (doc_id,), fetch="one", trace_name="CountSingleDocumentChunks"
    )
    return int((row or {}).get("ChunkCount") or 0)

def build_single_document_doc(conn, doc_id):
    chunk_limit = get_int_setting(conn, "MaxSingleDocumentChunks", 80)
    max_chars = get_int_setting(conn, "MaxSingleDocumentChunkChars", 3000)
    chunks = get_document_chunks(conn, doc_id, max_chars=max_chars, chunk_limit=chunk_limit)
    if not chunks:
        return None
    first = chunks[0]
    return {
        "DocID": first["DocID"], "FileName": first["FileName"], "FullPath": first["FullPath"],
        "Extension": first["Extension"], "FolderLevel1": first.get("FolderLevel1"),
        "FolderLevel2": first.get("FolderLevel2"), "FolderLevel3": first.get("FolderLevel3"),
        "BestScore": 1.0, "Top3AverageScore": 1.0, "RankingScore": 1.0, "Chunks": chunks,
    }

def run_single_document(conn, user_question, explicit_filename=None):
    set_execution_mode("SINGLE_DOCUMENT_SQL", "GPT_RAG")
    row = None
    if explicit_filename:
        matches = find_document_by_filename(conn, explicit_filename)
        if not matches:
            print(f"没有找到文件：{explicit_filename}")
            if CURRENT_EXECUTION is not None:
                CURRENT_EXECUTION["ExecutionStatus"] = "NoResults"
                CURRENT_EXECUTION["SearchQuery"] = explicit_filename
                CURRENT_EXECUTION["NewSearch"] = True
            return True

        best_rank = int(matches[0].get("MatchRank") or 0)
        best_matches = [m for m in matches if int(m.get("MatchRank") or 0) == best_rank]

        # Never silently choose between multiple equally good title matches.
        if len(best_matches) > 1:
            print(f"找到多个同名或近似文件，无法安全确定唯一文档：{explicit_filename}")
            for m in best_matches[:10]:
                print(f"- DocID={m.get('DocID')} | {m.get('FileName')} | {m.get('FullPath')}")
            if CURRENT_EXECUTION is not None:
                CURRENT_EXECUTION["ExecutionStatus"] = "Ambiguous"
                CURRENT_EXECUTION["SearchQuery"] = explicit_filename
                CURRENT_EXECUTION["NewSearch"] = True
                CURRENT_EXECUTION["RetrievalStats"] = {
                    "CandidateDocuments": len(best_matches),
                    "FilenameResolution": "AMBIGUOUS",
                }
            return True

        row = best_matches[0]
        set_current_document(row)
    elif THREAD.get("current_document_mode") and THREAD.get("current_doc_id"):
        row = {
            "DocID": THREAD["current_doc_id"],
            "FileName": THREAD.get("current_file_name"),
            "FullPath": THREAD.get("current_full_path"),
        }
    else:
        return False

    total_document_chunks = count_document_chunks(conn, row["DocID"])
    doc = build_single_document_doc(conn, row["DocID"])
    if not doc:
        print(f"文件已定位，但没有找到可用正文 Chunk：{row.get('FileName')}")
        return True

    THREAD["docs"] = [doc]
    stats = {
        "MatchedChunks": len(doc["Chunks"]), "ChunkLimit": len(doc["Chunks"]),
        "ChunksRetrieved": len(doc["Chunks"]), "UniqueDocumentsMatched": 1,
        "TopKDocuments": 1, "DocumentsSent": 1,
        "DocumentRankingMethod": "FIXED_DOC_ID",
        "CurrentDocID": row["DocID"], "CurrentFileName": row.get("FileName"),
        "TotalDocumentChunks": total_document_chunks,
        "DocumentChunksSent": len(doc["Chunks"]),
    }
    THREAD["retrieval_stats"] = stats
    if CURRENT_EXECUTION is not None:
        CURRENT_EXECUTION["RouterIntent"] = "SINGLE_DOCUMENT_ANALYSIS"
        CURRENT_EXECUTION["SearchMode"] = "SINGLE_DOCUMENT_SQL"
        CURRENT_EXECUTION["PresentationMode"] = "GPT_RAG"
        CURRENT_EXECUTION["SearchQuery"] = f"DocID={row['DocID']}"
        CURRENT_EXECUTION["NewSearch"] = True if explicit_filename else False
        CURRENT_EXECUTION["RetrievalStats"] = dict(stats)

    print("\nSearch Mode: SINGLE_DOCUMENT_SQL")
    print(f"Current document: {row.get('FileName')} | DocID={row.get('DocID')}")
    print(f"Chunks loaded: {len(doc['Chunks'])} / total {total_document_chunks}")
    if len(doc["Chunks"]) < total_document_chunks:
        print("Note: document input was capped by MaxSingleDocumentChunks.")
    print("Thinking...\n")

    result = answer_with_gpt(user_question, [doc], "RAG_ANSWER")
    answer = result["text"]
    print("=" * 100)
    print(answer)
    print("=" * 100)
    THREAD["history"].append({"question": user_question, "answer": answer})
    update_thread_summary(user_question, answer)
    log_question(conn, user_question, f"DocID={row['DocID']}", answer, [doc], 0,
                 intent="SINGLE_DOCUMENT_ANALYSIS", searched=True, retrieval_stats=stats,
                 llm_usage=result, prompt_chars=result.get("prompt_chars"),
                 max_chunks_per_doc=min(len(doc["Chunks"]), 20), max_chars_per_chunk=3000)
    return True


# =====================================================
# Scoped RAG
# =====================================================

RAG_SCOPE_MODES = {"AUTO", "HARD", "SOFT", "GLOBAL"}

SUPPORTED_SCOPE_KEYS = {
    "ticker",
    "fact_type",
    "form_type",
    "source",
    "sector",
    "country",
}


def _clean_scope_list(value):
    """Normalize a GPT-produced scalar/list into a unique list of strings."""
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]

    result = []
    seen = set()
    for item in value:
        s = str(item or "").strip()
        if not s:
            continue
        key = s.lower()
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


def extract_rag_scope(user_question):
    """
    Split a RAG question into structured metadata scope and semantic retrieval text.
    """
    prompt = f"""
You are a scope planner for a PostgreSQL + JSONB enterprise RAG system.

Return JSON only.

The Document table has a JSONB column named metadata.
Common metadata keys include:
- fact_type
- ticker
- form_type
- filing_date
- source
- sector
- country

The Document table also has a real column named CreatedAt.
CreatedAt means when the document record was added to this local knowledge base.
This is different from filing_date, which is the business/document date inside metadata.

Separate STRUCTURED SCOPE from SEMANTIC CONTENT.

Example:
User: 示例科技公司 2026 年 10-K / 10-Q 里的机会与风险

Return approximately:
{{
  "recommended_mode": "HARD",
  "scope": {{
    "ticker": ["DEMO"],
    "form_type": ["10-K", "10-Q"],
    "date_from": "2026-01-01",
    "date_to": "2026-12-31"
  }},
  "semantic_query": "opportunities risks growth drivers challenges uncertainties",
  "explicit_constraints": ["company", "year", "form_type"],
  "reason": "The user explicitly constrained company, year and filing type."
}}

Rules:
1. recommended_mode must be HARD, SOFT, or GLOBAL.
2. HARD only when the user clearly specifies restrictive scope.
3. SOFT when a useful scope exists but is incomplete, inferred, or ambiguous.
4. GLOBAL when the question intentionally spans the KB or has no useful scope.
5. You may normalize a well-known company name to ticker only when highly confident.
6. semantic_query should describe what CONTENT/TOPIC to find INSIDE the scoped documents.
7. If the user only asks for an operation such as summary, key points, highlights, overview, summarize, 要点, 总结, 概括, 展示要点, and does not specify a content topic, semantic_query MUST be an empty string. Task words are not retrieval keywords.
8. Remove metadata-only terms such as ticker/year/form type from semantic_query when possible.
9. date_from/date_to refer to the document/business date such as filing_date.
10. created_from/created_to refer to Document.CreatedAt, meaning when the file was added/indexed/scanned into this KB.
11. For phrases such as today, yesterday, this week, recently added, newly added, scanned today, indexed today, or 今天新增/今天添加/今天scan/今天入库, use created_from/created_to rather than date_from/date_to.
12. All date fields must be YYYY-MM-DD or null.
13. Scope values other than dates must be arrays.
14. Use only: ticker, fact_type, form_type, source, sector, country, date_from, date_to, created_from, created_to.
15. Do not answer the question.

Current local date: {datetime.now().date().isoformat()}

Return:
{{
  "recommended_mode": "HARD|SOFT|GLOBAL",
  "scope": {{
    "ticker": [],
    "fact_type": [],
    "form_type": [],
    "source": [],
    "sector": [],
    "country": [],
    "date_from": null,
    "date_to": null,
    "created_from": null,
    "created_to": null
  }},
  "semantic_query": "...",
  "explicit_constraints": [],
  "reason": "..."
}}

User question:
{user_question}

Current thread summary:
{THREAD["summary"]}
"""

    try:
        data = gpt_json([
            {"role": "system", "content": "You are a strict JSON RAG scope planner."},
            {"role": "user", "content": prompt},
        ])
    except Exception:
        return {
            "recommended_mode": "GLOBAL",
            "scope": {
                "ticker": [],
                "fact_type": [],
                "form_type": [],
                "source": [],
                "sector": [],
                "country": [],
                "date_from": None,
                "date_to": None,
                "created_from": None,
                "created_to": None,
            },
            "semantic_query": user_question,
            "explicit_constraints": [],
            "reason": "Scope planner failed; safe fallback to GLOBAL.",
        }

    raw_scope = data.get("scope") or {}
    scope = {}

    for key in SUPPORTED_SCOPE_KEYS:
        scope[key] = _clean_scope_list(raw_scope.get(key))

    for key in ("date_from", "date_to", "created_from", "created_to"):
        value = raw_scope.get(key)
        scope[key] = str(value).strip() if value else None

    recommended = str(data.get("recommended_mode") or "GLOBAL").upper()
    if recommended not in {"HARD", "SOFT", "GLOBAL"}:
        recommended = "GLOBAL"

    semantic_query = str(data.get("semantic_query") or "").strip()

    return {
        "recommended_mode": recommended,
        "scope": scope,
        "semantic_query": semantic_query,
        "explicit_constraints": _clean_scope_list(data.get("explicit_constraints")),
        "reason": str(data.get("reason") or "").strip(),
    }


def _scope_has_constraints(scope):
    scope = scope or {}
    return (
        any(scope.get(k) for k in SUPPORTED_SCOPE_KEYS)
        or bool(scope.get("date_from"))
        or bool(scope.get("date_to"))
        or bool(scope.get("created_from"))
        or bool(scope.get("created_to"))
    )


def _build_scope_sql(scope, mode):
    """
    HARD -> metadata WHERE predicates.
    SOFT -> metadata ranking bonus, without exclusion.
    GLOBAL -> no scope effect.
    """
    mode = (mode or "GLOBAL").upper()
    scope = scope or {}

    hard_parts = []
    hard_params = []
    soft_score_parts = []
    soft_score_params = []

    def add_jsonb_values(key, values, bonus):
        values = _clean_scope_list(values)
        if not values:
            return

        placeholders = ", ".join(["%s"] * len(values))

        if mode == "HARD":
            hard_parts.append(
                f"LOWER(COALESCE(d.metadata->>'{key}', '')) IN ({placeholders})"
            )
            hard_params.extend([v.lower() for v in values])

        elif mode == "SOFT":
            soft_score_parts.append(
                f"(CASE WHEN LOWER(COALESCE(d.metadata->>'{key}', '')) "
                f"IN ({placeholders}) THEN {float(bonus):.4f} ELSE 0 END)"
            )
            soft_score_params.extend([v.lower() for v in values])

    add_jsonb_values("ticker", scope.get("ticker"), 6.0)
    add_jsonb_values("fact_type", scope.get("fact_type"), 4.0)
    add_jsonb_values("form_type", scope.get("form_type"), 5.0)
    add_jsonb_values("source", scope.get("source"), 3.0)
    add_jsonb_values("sector", scope.get("sector"), 2.0)
    add_jsonb_values("country", scope.get("country"), 2.0)

    date_from = scope.get("date_from")
    date_to = scope.get("date_to")

    # Accept both the standard spelling filing_date and the common historical
    # typo filling_date so older metadata can still participate in scope filtering.
    metadata_date_expr = (
        "COALESCE("
        "d.metadata->>'filing_date', "
        "d.metadata->>'filling_date', "
        "d.metadata->>'filed_date', "
        "'')"
    )
    date_expr = (
        f"CASE WHEN {metadata_date_expr} "
        "~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
        f"THEN ({metadata_date_expr})::date ELSE NULL END"
    )

    if date_from:
        if mode == "HARD":
            hard_parts.append(f"{date_expr} >= %s::date")
            hard_params.append(date_from)
        elif mode == "SOFT":
            soft_score_parts.append(
                f"(CASE WHEN {date_expr} >= %s::date THEN 3.0000 ELSE 0 END)"
            )
            soft_score_params.append(date_from)

    if date_to:
        if mode == "HARD":
            hard_parts.append(f"{date_expr} <= %s::date")
            hard_params.append(date_to)
        elif mode == "SOFT":
            soft_score_parts.append(
                f"(CASE WHEN {date_expr} <= %s::date THEN 3.0000 ELSE 0 END)"
            )
            soft_score_params.append(date_to)

    # Knowledge-base ingestion date. Document.CreatedAt records when the document
    # was first added to this KB; do not confuse it with metadata filing_date.
    created_from = scope.get("created_from")
    created_to = scope.get("created_to")

    if created_from:
        if mode == "HARD":
            hard_parts.append("d.CreatedAt >= %s::date")
            hard_params.append(created_from)
        elif mode == "SOFT":
            soft_score_parts.append(
                "(CASE WHEN d.CreatedAt >= %s::date THEN 4.0000 ELSE 0 END)"
            )
            soft_score_params.append(created_from)

    if created_to:
        # Use an exclusive next-day boundary so the entire calendar day is included
        # regardless of the time component stored in CreatedAt.
        if mode == "HARD":
            hard_parts.append("d.CreatedAt < (%s::date + INTERVAL '1 day')")
            hard_params.append(created_to)
        elif mode == "SOFT":
            soft_score_parts.append(
                "(CASE WHEN d.CreatedAt < (%s::date + INTERVAL '1 day') THEN 4.0000 ELSE 0 END)"
            )
            soft_score_params.append(created_to)

    hard_sql = " AND ".join(hard_parts) if hard_parts else "TRUE"
    soft_score_sql = (
        "(" + " + ".join(soft_score_parts) + ")"
        if soft_score_parts else "0.0"
    )

    return hard_sql, hard_params, soft_score_sql, soft_score_params


def count_scoped_documents(conn, scope, mode):
    if mode != "HARD":
        return None

    scope_sql, scope_params, _, _ = _build_scope_sql(scope, "HARD")
    row = execute_select(
        conn,
        f"""
        SELECT COUNT(*) AS ScopedDocuments
        FROM Document d
        WHERE {scope_sql};
        """,
        tuple(scope_params),
        fetch="one",
        trace_name="CountScopedDocuments",
    )
    return int((row or {}).get("ScopedDocuments") or 0)


def count_scoped_chunks(conn, scope, mode):
    if mode != "HARD":
        return None

    scope_sql, scope_params, _, _ = _build_scope_sql(scope, "HARD")
    row = execute_select(
        conn,
        f"""
        SELECT COUNT(*) AS ScopedChunks
        FROM DocumentChunk c
        JOIN Document d ON d.DocID = c.DocID
        WHERE {scope_sql};
        """,
        tuple(scope_params),
        fetch="one",
        trace_name="CountScopedChunks",
    )
    return int((row or {}).get("ScopedChunks") or 0)


def plan_rag_scope(user_question, requested_mode="AUTO"):
    requested_mode = (requested_mode or "AUTO").upper()
    if requested_mode not in RAG_SCOPE_MODES:
        requested_mode = "AUTO"

    plan = extract_rag_scope(user_question)

    effective_mode = (
        plan["recommended_mode"]
        if requested_mode == "AUTO"
        else requested_mode
    )

    # A forced HARD/SOFT with no usable metadata cannot do meaningful scoping.
    if effective_mode in {"HARD", "SOFT"} and not _scope_has_constraints(plan["scope"]):
        effective_mode = "GLOBAL"

    plan["requested_mode"] = requested_mode
    plan["effective_mode"] = effective_mode
    return plan


# =====================================================
# RAG Search
# =====================================================

def normalize_rag_search_terms(search_query, max_terms=12):
    """
    Convert the rewritten RAG query into PostgreSQL text-search terms.

    This deliberately does not depend on pg_trgm or a language-specific
    PostgreSQL dictionary, so mixed Chinese/English KB content works immediately
    after the MySQL -> PostgreSQL migration.
    """
    raw = (search_query or "").strip()
    if not raw:
        return []

    # Generic tokenizer for RAG/SEARCH terms (independent of the removed CONTENT route).
    tokens = re.findall(r'"([^"]+)"|“([^”]+)”|\'([^\']+)\'|([^\s,，、;；|/]+)', raw)
    terms = []
    for groups in tokens:
        term = next((g for g in groups if g), "").strip(" .。!?！？:：()（）[]【】{}<>《》")
        if term:
            terms.append(term)

    # Keep useful terms only. Very short ASCII terms create too many matches;
    # one-character CJK terms are allowed because they may be meaningful.
    result = []
    seen = set()

    for term in terms:
        term = str(term).strip()
        if not term:
            continue

        if term.isascii() and len(term) < 2:
            continue

        key = term.lower()
        if key in seen:
            continue

        seen.add(key)
        result.append(term)

        if len(result) >= max_terms:
            break

    # If normalization removed everything, use the original query as one phrase.
    if not result and raw:
        result = [raw]

    return result


def _build_postgres_chunk_match(search_query):
    """
    Build a broad PostgreSQL bilingual text predicate and relevance score.

    Match behavior intentionally resembles MySQL NATURAL LANGUAGE retrieval:
    any important term can make a chunk a candidate; chunks matching more terms
    receive a higher Score and therefore rank first.
    """
    terms = normalize_rag_search_terms(search_query)

    if not terms:
        return "FALSE", "0.0", [], []

    where_parts = []
    score_parts = []
    where_params = []
    score_params = []

    for term in terms:
        pattern = f"%{term}%"
        where_parts.append("COALESCE(c.ChunkText, '') ILIKE %s")
        where_params.append(pattern)

        # Longer terms/phrases are slightly more informative.
        weight = 1.0 + min(len(term), 20) / 20.0
        score_parts.append(
            f"(CASE WHEN COALESCE(c.ChunkText, '') ILIKE %s "
            f"THEN {weight:.4f} ELSE 0 END)"
        )
        score_params.append(pattern)

    return (
        "(" + " OR ".join(where_parts) + ")",
        "(" + " + ".join(score_parts) + ")",
        where_params,
        score_params,
    )


def search_chunks(conn, search_query, limit=120, scope=None, scope_mode="GLOBAL"):
    where_sql, score_sql, where_params, score_params = _build_postgres_chunk_match(
        search_query
    )

    scope_mode = (scope_mode or "GLOBAL").upper()
    hard_scope_sql, hard_scope_params, soft_score_sql, soft_score_params = _build_scope_sql(
        scope or {}, scope_mode
    )

    combined_score_sql = score_sql
    if scope_mode == "SOFT" and soft_score_sql != "0.0":
        combined_score_sql = f"({score_sql} + {soft_score_sql})"

    sql = f"""
    SELECT
        c.ChunkID,
        d.DocID,
        d.FileName,
        d.FullPath,
        d.Extension,
        d.FolderLevel1,
        d.FolderLevel2,
        d.FolderLevel3,
        c.ChunkNo,
        c.ChunkSource,
        c.PageNo,
        c.SheetName,
        LEFT(c.ChunkText, 1600) AS TextSample,
        {combined_score_sql} AS Score
    FROM DocumentChunk c
    JOIN Document d ON c.DocID = d.DocID
    WHERE {where_sql}
      AND ({hard_scope_sql if scope_mode == "HARD" else "TRUE"})
    ORDER BY Score DESC, c.ChunkID
    LIMIT %s;
    """

    params = list(score_params)
    if scope_mode == "SOFT":
        params.extend(soft_score_params)
    params.extend(where_params)
    if scope_mode == "HARD":
        params.extend(hard_scope_params)
    params.append(limit)

    return execute_select(
        conn,
        sql,
        tuple(params),
        trace_name=f"SearchChunks_{scope_mode}"
    )


def count_matching_chunks(conn, search_query, scope=None, scope_mode="GLOBAL"):
    """Count total text-matching chunks before LIMIT, respecting HARD scope."""
    where_sql, _, where_params, _ = _build_postgres_chunk_match(search_query)
    scope_mode = (scope_mode or "GLOBAL").upper()
    hard_scope_sql, hard_scope_params, _, _ = _build_scope_sql(scope or {}, scope_mode)

    sql = f"""
    SELECT COUNT(*) AS MatchedChunks
    FROM DocumentChunk c
    JOIN Document d ON d.DocID = c.DocID
    WHERE {where_sql}
      AND ({hard_scope_sql if scope_mode == "HARD" else "TRUE"});
    """

    params = list(where_params)
    if scope_mode == "HARD":
        params.extend(hard_scope_params)

    row = execute_select(
        conn,
        sql,
        tuple(params),
        fetch="one",
        trace_name=f"CountMatchingChunks_{scope_mode}"
    )
    return int((row or {}).get("MatchedChunks") or 0)


def group_chunks_to_docs(chunks, doc_limit=50, score_top_n=3):
    """
    Group candidate chunks by document and rank documents using the average
    score of their top N matching chunks.

    Why:
    - BestScore alone can over-rank a document that has only one strong chunk.
    - Top-N average rewards documents with several consistently relevant chunks.
    """
    docs = {}

    for r in chunks:
        doc_id = r["DocID"]
        score = float(r["Score"] or 0)

        if doc_id not in docs:
            docs[doc_id] = {
                "DocID": r["DocID"],
                "FileName": r["FileName"],
                "FullPath": r["FullPath"],
                "Extension": r["Extension"],
                "FolderLevel1": r["FolderLevel1"],
                "FolderLevel2": r["FolderLevel2"],
                "FolderLevel3": r["FolderLevel3"],
                "BestScore": score,
                "Top3AverageScore": 0.0,
                "RankingScore": 0.0,
                "Chunks": [],
            }

        docs[doc_id]["Chunks"].append(r)

        if score > docs[doc_id]["BestScore"]:
            docs[doc_id]["BestScore"] = score

    result = []
    for doc in docs.values():
        # Keep chunks ordered from highest to lowest score inside each document.
        doc["Chunks"].sort(
            key=lambda c: float(c.get("Score") or 0),
            reverse=True,
        )

        top_scores = [
            float(c.get("Score") or 0)
            for c in doc["Chunks"][:score_top_n]
        ]

        ranking_score = (
            sum(top_scores) / len(top_scores)
            if top_scores
            else 0.0
        )

        # Keep both names for clear display/logging and future flexibility.
        doc["Top3AverageScore"] = ranking_score
        doc["RankingScore"] = ranking_score
        result.append(doc)

    result.sort(
        key=lambda x: (x["RankingScore"], x["BestScore"]),
        reverse=True,
    )
    return result[:doc_limit]


def extract_explicit_semantic_override(user_question):
    """
    <concept> is a power-user override for semantic content retrieval.

    Returns:
      cleaned_question: question with the first <...> removed
      has_override:     True when angle brackets were explicitly supplied
      concept:          text inside <...>; empty string means force no semantic filtering
    """
    text = str(user_question or "")
    match = re.search(r"<([^<>]*)>", text)
    if not match:
        return text, False, None

    concept = match.group(1).strip()
    cleaned = (text[:match.start()] + " " + text[match.end():]).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned, True, concept


def get_scoped_chunks(conn, scope, limit=500, max_chars=3000):
    """Directly load chunks from a HARD metadata scope without semantic keyword filtering."""
    hard_scope_sql, hard_scope_params, _, _ = _build_scope_sql(scope or {}, "HARD")
    sql = f"""
    SELECT
        c.ChunkID,
        d.DocID,
        d.FileName,
        d.FullPath,
        d.Extension,
        d.FolderLevel1,
        d.FolderLevel2,
        d.FolderLevel3,
        c.ChunkNo,
        c.ChunkSource,
        c.PageNo,
        c.SheetName,
        LEFT(c.ChunkText, %s) AS TextSample,
        1.0 AS Score
    FROM DocumentChunk c
    JOIN Document d ON c.DocID = d.DocID
    WHERE {hard_scope_sql}
    ORDER BY d.DocID, c.ChunkNo, c.ChunkID
    LIMIT %s;
    """
    params = [max_chars]
    params.extend(hard_scope_params)
    params.append(limit)
    return execute_select(conn, sql, tuple(params), trace_name="DirectScopedChunks_HARD")


def retrieve_docs(conn, user_question, force_new_search=False, rag_scope_mode="AUTO"):
    if THREAD["docs"] and not force_new_search:
        return THREAD["docs"], THREAD["last_search_query"], False, THREAD.get("retrieval_stats", {})

    planner_question, has_semantic_override, semantic_override = extract_explicit_semantic_override(user_question)
    scope_plan = plan_rag_scope(planner_question, requested_mode=rag_scope_mode)
    effective_mode = scope_plan["effective_mode"]
    scope = scope_plan["scope"]

    # <...> explicitly controls the semantic content concept.
    # <tariff> -> OpenAI expands/translates the concept into retrieval terms.
    # <>     -> explicitly disables semantic keyword filtering.
    if has_semantic_override:
        if semantic_override:
            rewritten = rewrite_search_query(semantic_override)
            search_query = (rewritten.get("search_query") or semantic_override).strip()
        else:
            search_query = ""
    else:
        search_query = (scope_plan.get("semantic_query") or "").strip()

    if effective_mode == "GLOBAL":
        # GLOBAL still needs a content query. If the planner returned no topic,
        # use the natural-language question as the normal global search input.
        if not search_query:
            rewritten = rewrite_search_query(planner_question)
            search_query = rewritten["search_query"]
            topic_title = rewritten["topic_title"]
        elif has_semantic_override:
            topic_title = planner_question[:120] or "Topic"
        else:
            rewritten = rewrite_search_query(search_query)
            search_query = rewritten["search_query"]
            topic_title = rewritten["topic_title"]
    else:
        topic_title = planner_question[:120] or "Topic"

    top_docs = get_int_setting(conn, "TopKDocuments", 50)
    chunk_limit = max(200, top_docs * 10) if effective_mode == "HARD" else max(500, top_docs * 10)

    scoped_documents = count_scoped_documents(conn, scope, effective_mode)
    scoped_chunks = count_scoped_chunks(conn, scope, effective_mode)

    direct_scoped_retrieval = (
        effective_mode == "HARD"
        and _scope_has_constraints(scope)
        and not search_query
    )

    if direct_scoped_retrieval:
        # No content topic was requested. The metadata scope itself identifies
        # the documents, so do not search for task words such as summary/key points.
        # Load scoped chunks directly for GPT summarization/reasoning.
        direct_limit = max(chunk_limit, min(int(scoped_chunks or 0), 1000))
        chunks = get_scoped_chunks(conn, scope, limit=direct_limit, max_chars=3000)
        matched_chunks = len(chunks)
        context_chunks_per_doc = 20
        ranking_method = "HARD_METADATA_DIRECT_SCOPED_CHUNKS"
    else:
        matched_chunks = count_matching_chunks(
            conn,
            search_query,
            scope=scope,
            scope_mode=effective_mode,
        )
        chunks = search_chunks(
            conn,
            search_query,
            limit=chunk_limit,
            scope=scope,
            scope_mode=effective_mode,
        )
        context_chunks_per_doc = 3
        ranking_method = (
            "HARD_METADATA_FILTER_THEN_TOP_3_CHUNK_AVERAGE"
            if effective_mode == "HARD"
            else "SOFT_METADATA_BOOST_PLUS_TOP_3_CHUNK_AVERAGE"
            if effective_mode == "SOFT"
            else "GLOBAL_TOP_3_CHUNK_AVERAGE"
        )

    unique_docs_matched = len(set(r["DocID"] for r in chunks))
    docs = group_chunks_to_docs(chunks, doc_limit=top_docs, score_top_n=3)

    stats = {
        "RAGScopeRequestedMode": scope_plan.get("requested_mode"),
        "RAGScopeMode": effective_mode,
        "RAGScopeRecommendedMode": scope_plan.get("recommended_mode"),
        "RAGScope": scope,
        "RAGScopeExplicitConstraints": scope_plan.get("explicit_constraints"),
        "RAGScopeReason": scope_plan.get("reason"),
        "SemanticOverridePresent": has_semantic_override,
        "SemanticOverrideConcept": semantic_override if has_semantic_override else None,
        "SemanticQuery": search_query,
        "DirectScopedRetrieval": direct_scoped_retrieval,
        "ScopedDocuments": scoped_documents,
        "ScopedChunks": scoped_chunks,
        "MatchedChunks": matched_chunks,
        "ChunkLimit": chunk_limit,
        "ChunksRetrieved": len(chunks),
        "UniqueDocumentsMatched": unique_docs_matched,
        "TopKDocuments": top_docs,
        "DocumentsSent": len(docs),
        "ContextChunksPerDoc": context_chunks_per_doc,
        "DocumentRankingMethod": ranking_method,
    }

    THREAD["docs"] = docs
    THREAD["title"] = topic_title
    THREAD["last_search_query"] = search_query
    THREAD["retrieval_stats"] = stats
    THREAD["last_rag_scope"] = scope
    THREAD["last_rag_scope_mode"] = effective_mode
    THREAD["last_semantic_query"] = search_query

    return docs, search_query, True, stats


# =====================================================
# Context
# =====================================================

def build_docs_context(docs, max_chunks_per_doc=3):
    parts = []

    for i, d in enumerate(docs, 1):
        text = f"""
==============================
Document {i}
FileName: {d['FileName']}
Path: {d['FullPath']}
Extension: {d['Extension']}
Folder: {d.get('FolderLevel1')} / {d.get('FolderLevel2')} / {d.get('FolderLevel3')}
RankingScore (Top 3 Chunk Average): {d.get('RankingScore', d['BestScore'])}
BestScore: {d['BestScore']}

Relevant excerpts:
"""

        for c in d["Chunks"][:max_chunks_per_doc]:
            text += f"""
--- Chunk {c['ChunkNo']} | Source={c['ChunkSource']} | Page={c['PageNo']} | Sheet={c['SheetName']} ---
{c['TextSample']}
"""

        parts.append(text)

    return "\n".join(parts)


def build_history_context(max_turns=6):
    recent = THREAD["history"][-max_turns:]
    text = ""

    for i, h in enumerate(recent, 1):
        text += f"""
Turn {i}
User: {h['question']}
Assistant: {h['answer'][:1200]}
"""

    return text


def mode_instruction(intent):
    mapping = {
        "RAG_ANSWER": "Answer the user's question using retrieved document context.",
        "CATALOG": "Create an index/catalog organized by category, year, company, subject, and document type.",
    }

    return mapping.get(intent, mapping["RAG_ANSWER"])


def answer_with_gpt(user_question, docs, intent):
    prompt = f"""
You are a bilingual enterprise knowledge-base RAG agent.

Rules:
1. Use only retrieved document context and conversation context.
2. Do not invent facts.
3. If evidence is weak or missing, say so clearly.
   Never claim that the whole database has no such document merely because the
   retrieved context is missing it. Say "当前检索结果中未找到" unless scope diagnostics
   explicitly establish the absence.
4. Always cite source file names.
5. If the user asks Chinese, answer Chinese.
6. Be practical and business-oriented.
7. For follow-up questions, continue the current topic unless the user changes topic.

Current thread title:
{THREAD['title']}

Thread summary:
{THREAD['summary']}

Recent conversation:
{build_history_context()}

Task:
{mode_instruction(intent)}

Current user question:
{user_question}

Retrieved document context:
{build_docs_context(docs, max_chunks_per_doc=(len(docs[0]["Chunks"]) if len(docs)==1 and THREAD.get("current_document_mode") else int((THREAD.get("retrieval_stats") or {}).get("ContextChunksPerDoc") or 3)))}
"""

    result = gpt_call([
        {"role": "system", "content": "You are an enterprise knowledge-base RAG assistant."},
        {"role": "user", "content": prompt},
    ])
    result["prompt_chars"] = len(prompt)
    result["prompt_text"] = prompt
    return result


def update_thread_summary(question, answer):
    prompt = f"""
Update the conversation summary for this thread.

Current summary:
{THREAD['summary']}

New question:
{question}

New answer:
{answer}

Return concise bullet points.
"""

    THREAD["summary"] = gpt_text([
        {"role": "system", "content": "You summarize conversation state for a RAG assistant."},
        {"role": "user", "content": prompt},
    ], temperature=0.1)


# =====================================================
# Logging
# =====================================================

def log_question(
    conn,
    question,
    search_query,
    answer,
    docs,
    elapsed_ms,
    error=None,
    intent=None,
    searched=None,
    retrieval_stats=None,
    llm_usage=None,
    prompt_chars=None,
    max_chunks_per_doc=3,
    max_chars_per_chunk=1600,
):
    """
    Compatibility wrapper for the old RAG logging call.
    It now enriches CURRENT_EXECUTION; write_execution_log() performs the
    single final INSERT into AgentExecutionLog.
    """
    if CURRENT_EXECUTION is None:
        return

    documents_used, chunks_used = build_rag_log_details(
        docs,
        max_chunks_per_doc=max_chunks_per_doc
    )

    CURRENT_EXECUTION["RouterIntent"] = intent or CURRENT_EXECUTION.get("RouterIntent")
    if intent == "SINGLE_DOCUMENT_ANALYSIS":
        CURRENT_EXECUTION["SearchMode"] = "SINGLE_DOCUMENT_SQL"
    elif not CURRENT_EXECUTION.get("SearchMode"):
        scope_mode = (retrieval_stats or {}).get("RAGScopeMode")
        CURRENT_EXECUTION["SearchMode"] = (
            f"RAG_{scope_mode}_FULLTEXT" if scope_mode else "RAG_FULLTEXT"
        )
    CURRENT_EXECUTION["PresentationMode"] = "GPT_RAG"
    CURRENT_EXECUTION["SearchQuery"] = search_query
    CURRENT_EXECUTION["NewSearch"] = searched
    CURRENT_EXECUTION["RetrievalStats"] = dict(retrieval_stats or {})
    CURRENT_EXECUTION["RetrievalStats"]["MaxChunksPerDoc"] = max_chunks_per_doc
    CURRENT_EXECUTION["RetrievalStats"]["MaxCharsPerChunk"] = max_chars_per_chunk
    CURRENT_EXECUTION["DocumentsUsed"] = documents_used
    CURRENT_EXECUTION["ChunksUsed"] = chunks_used
    CURRENT_EXECUTION["FinalAnswer"] = answer

    if error:
        CURRENT_EXECUTION["ExecutionStatus"] = "Failed"
        CURRENT_EXECUTION["ErrorStage"] = "RAG"
        CURRENT_EXECUTION["ErrorType"] = "RAGExecutionError"
        CURRENT_EXECUTION["ErrorMessage"] = error


# =====================================================
# Display helpers
# =====================================================

MAX_GPT_ROWS = 80
MAX_GPT_CELL_CHARS = 240


def compact_rows_for_gpt(rows, max_rows=MAX_GPT_ROWS):
    """Keep GPT presentation input small and predictable."""
    safe_rows = []
    for r in (rows or [])[:max_rows]:
        item = {}
        for k, v in r.items():
            if v is None:
                item[k] = ""
            else:
                text = str(v).replace("\r", " ").replace("\n", " ")
                item[k] = text[:MAX_GPT_CELL_CHARS]
        safe_rows.append(item)
    return safe_rows


def format_rows_with_gpt(user_question, rows, title="SQL Result", max_rows=MAX_GPT_ROWS):
    """
    Presentation layer: SQL/Search finds data; GPT formats small result sets into clean text.
    Never send huge raw metadata tables to GPT.
    """
    if not rows:
        return "没有找到结果。"

    shown_rows = compact_rows_for_gpt(rows, max_rows=max_rows)
    total_rows = len(rows)

    prompt = f"""
你是企业知识库 AI Agent 的输出层助手。

用户问题：
{user_question}

结果标题：
{title}

Python/SQL 已经返回以下结构化结果。请只基于这些结果回答，不要编造：
{json.dumps(shown_rows, ensure_ascii=False, default=str)}

显示信息：
- Total rows returned by SQL/Python: {total_rows}
- Rows shown to you: {len(shown_rows)}

输出要求：
1. 用户用中文问，就用中文回答。
2. 优先使用 ChatGPT 风格的 Markdown 表格，不要输出 raw JSON。
3. 如果是统计结果，给出总数、主要分类、占比或简短说明。
4. 如果结果很多，只展示最重要/前面的记录，并说明“仅显示前 N 条”。
5. 不要说你读取了全文，除非输入里明确包含正文 excerpts。
6. 结尾可以给一个很短的下一步建议。
"""

    try:
        return gpt_text([
            {"role": "system", "content": "You format SQL/search results into clean ChatGPT-style text and markdown tables."},
            {"role": "user", "content": prompt},
        ], temperature=0.1)
    except Exception as e:
        # Safe fallback if GPT is rate-limited.
        lines = [f"{title}（GPT格式化失败，使用备用输出：{e}）"]
        for r in shown_rows:
            lines.append(" | ".join(f"{k}={v}" for k, v in r.items()))
        return "\n".join(lines)


def print_rows(rows, user_question="请展示以下结果", title="SQL Result", max_rows=MAX_GPT_ROWS):
    print(format_rows_with_gpt(user_question, rows, title=title, max_rows=max_rows))


def print_help():
    print("""
Commands:

Multi-line Ask:
<<<
请分析这个主题
1. 总结
2. Potential customers
3. Action plan
>>>

/help
/new
/clear
/thread
/stats
/types
/folders
/files
/files coffee
/latest
/summaries
/summaries keyword
/search sample keyword
/refresh question
/catalog 把所有文件分类统计一下

============================================================
强制 Route Switch（必须写在问题最前面，PREFIX: 后面接问题）
============================================================

RAG: question
  强制走 RAG_ANSWER，并且强制重新检索；Scope=AUTO。
  Agent 自动判断 HARD / SOFT / GLOBAL。
  例：RAG: 示例科技公司 2026 年 10-K / 10-Q 里的机会与风险

RAGH: question
  Advanced：强制 HARD scope。先严格按 metadata 缩小文档范围，再在范围内 RAG。
  例：RAGH: 示例科技公司 2026 年 10-K / 10-Q 里的机会与风险

RAGS: question
  Advanced：强制 SOFT scope。metadata 只做 ranking boost，不排除范围外文档。
  例：RAGS: 示例科技公司最近有哪些主要风险？

RAGG: question
  Advanced：强制 GLOBAL scope。忽略 metadata scope，直接全库 RAG。
  例：RAGG: 最近企业普遍担心哪些经营风险？

Aliases:
  RAG-AUTO / RAG-HARD / RAG-SOFT / RAG-GLOBAL

SEARCH: keywords
  强制走 SEARCH_ONLY，只做 raw PostgreSQL text keyword search，不让 GPT 综合回答。
  例：SEARCH: Example Customer packaging requirements

FILES: keyword
  强制走 FILE_LIST，只按文件 metadata（文件名/path/folder/extension 等）查找。
  例：FILES: Example Customer

CATALOG: question
  强制走 CATALOG。
  例：CATALOG: 把知识库资料按主题分类

IMPORTANT:
- Prefix 不区分大小写，例如 rag: / RAG: 都可以。
- Prefix 只在问题最前面生效。
- 没有以上预定义 Prefix 时，不做任何强制，完全使用现有 automatic router。
- 想“得到答案”而不是“找文件”时，最直接使用 RAG:。
- RAG: 默认 AUTO；只有调试/高级控制时才需要 RAGH:/RAGS:/RAGG:。

Without a forced prefix, normal natural language and the automatic router still work:
我一共有多少文件？
PDF有多少？
有哪些项目资料？
SEARCH: 某家公司
总结某个项目。
""")


def reset_thread():
    global THREAD
    THREAD = {
        "id": str(uuid.uuid4()),
        "title": "New Topic",
        "summary": "",
        "history": [],
        "docs": [],
        "last_search_query": "",
        "retrieval_stats": {},
        "current_doc_id": None,
        "current_file_name": "",
        "current_full_path": "",
        "current_document_mode": False,
        "last_rag_scope": {},
        "last_rag_scope_mode": "AUTO",
        "last_semantic_query": "",
    }


def show_thread():
    print("\nThread ID:", THREAD["id"])
    print("Title:", THREAD["title"])
    print("Last search:", THREAD["last_search_query"])
    print("Current document mode:", THREAD.get("current_document_mode"))
    print("Current DocID:", THREAD.get("current_doc_id"))
    print("Current file:", THREAD.get("current_file_name") or "(none)")
    print("Last RAG scope mode:", THREAD.get("last_rag_scope_mode") or "(none)")
    print("Last RAG scope:", json.dumps(THREAD.get("last_rag_scope") or {}, ensure_ascii=False))
    print("Last semantic query:", THREAD.get("last_semantic_query") or "(none)")
    print("\nSummary:")
    print(THREAD["summary"] or "(empty)")
    print("\nRetrieved documents:")
    for d in THREAD["docs"]:
        print(
            f"- {d['FileName']} | "
            f"RankingScore={d.get('RankingScore', d['BestScore']):.4f} | "
            f"BestScore={d['BestScore']:.4f}"
        )


# =====================================================
# Executors
# =====================================================

def run_metadata(conn, user_question, intent, route):
    set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
    if intent == "FILE_COUNT":
        print("\nSearch Mode: METADATA_SQL")
        print("Presentation: GPT text / markdown\n")
        print_rows([get_file_count(conn)], user_question, title="File count summary")
        return True

    if intent == "TYPE_COUNT":
        print("\nSearch Mode: METADATA_SQL")
        print("Presentation: GPT text / markdown\n")
        print_rows(get_type_count(conn), user_question, title="File type statistics")
        return True

    if intent == "FOLDER_CATALOG":
        print("\nSearch Mode: METADATA_SQL")
        print("Presentation: GPT text / markdown\n")
        print_rows(get_folder_catalog(conn, limit=120), user_question, title="Folder/category catalogue")
        return True

    if intent == "FILE_LIST":
        print("\nSearch Mode: METADATA_SQL")
        print("Presentation: GPT text / markdown\n")

        # Sorting-style metadata question:
        # Do not treat phrases such as "latest updates" as filename keywords.
        if is_latest_modified_question(user_question):
            rows = get_latest_modified_files(conn, limit=1)
            print_rows(
                rows,
                user_question,
                title="Latest modified file",
                max_rows=1
            )
            return True

        # Minimal category resolver:
        # Questions such as "What files are in the 2025 projects?" refer to FolderLevel1,
        # not to a literal filename phrase such as "files in 2025 projects".
        category = resolve_folder_level1(conn, user_question)
        if not category:
            category = resolve_folder_level1(conn, route.get("search_query", ""))

        if category:
            rows = list_files_by_folder_level1(conn, category, limit=120)
            print_rows(
                rows,
                user_question,
                title=f"Files in category: {category}",
                max_rows=80
            )
            return True

        # Normal keyword-based file listing.
        keyword = route.get("search_query", "").strip()
        if keyword.lower() in ("list files", "files", "file list"):
            keyword = None

        rows = list_files(conn, keyword=keyword, limit=120)
        print_rows(rows, user_question, title="File list", max_rows=80)
        return True

    return False


def run_catalog_text(conn, user_question):
    set_execution_mode("CATALOG_SQL_AGGREGATION", "GPT_MARKDOWN")
    """
    CATALOG intent should not dump all document metadata into GPT.
    It sends compact SQL aggregation to GPT for a clean ChatGPT-style answer.
    """
    print("\nSearch Mode: CATALOG_SQL_AGGREGATION")
    print("Source: Document metadata aggregated by SQL")
    print("Presentation: GPT text / markdown")
    print("Thinking...\n")

    q = user_question.lower()

    # For classification/statistics questions, category aggregation is the best default.
    if any(k in q for k in ["分类", "category", "categories", "统计", "count", "group", "按类别"]):
        rows = get_category_stats(conn, level=1, limit=80)
        print_rows(rows, user_question, title="Category statistics", max_rows=80)
        return True

    # For general catalogue/index questions, send compact summary sections instead of 1810 rows.
    summary = get_catalog_summary_for_gpt(conn)
    pseudo_rows = [
        {"Section": "File Count", "Data": json.dumps(summary["file_count"], ensure_ascii=False, default=str)},
        {"Section": "Top Categories", "Data": json.dumps(summary["category_stats"], ensure_ascii=False, default=str)},
        {"Section": "File Types", "Data": json.dumps(summary["type_stats"], ensure_ascii=False, default=str)},
    ]
    print_rows(pseudo_rows, user_question, title="Catalogue summary", max_rows=20)
    return True


def run_search_only(conn, user_question):
    set_execution_mode("SEARCH_ONLY", "RAW_TEXT")
    rewritten = rewrite_search_query(user_question)

    # The GUI should highlight the same original/expanded terms that core
    # actually uses for PostgreSQL matching, rather than guessing them.
    highlight_terms = normalize_rag_search_terms(rewritten["search_query"])
    original_terms = normalize_rag_search_terms(user_question)

    set_gui_highlight_terms(original_terms + highlight_terms)

    chunks = search_chunks(conn, rewritten["search_query"], limit=50)

    # Classic SEARCH results also expose their source documents to GUI.
    seen_docs = {}
    for row in chunks:
        doc_id = row.get("DocID")
        key = doc_id if doc_id is not None else row.get("FullPath")
        if key not in seen_docs:
            seen_docs[key] = {
                "DocID": row.get("DocID"),
                "FileName": row.get("FileName"),
                "FullPath": row.get("FullPath"),
                "BestScore": row.get("Score"),
                "RankingScore": row.get("Score"),
            }
    set_gui_retrieved_documents(list(seen_docs.values()))

    print(
        "\nSearch query:",
        console_highlight(
            rewritten["search_query"],
            get_gui_result().get("highlight_terms", []),
        ),
    )
    print("Chunks found:", len(chunks))

    for i, r in enumerate(chunks, 1):
        print("=" * 100)
        print(f"{i}. Score={r['Score']} | File={r['FileName']} | Chunk={r['ChunkNo']}")
        print(f"Path: {r['FullPath']}")
        print("-" * 100)
        print(
            console_highlight(
                r["TextSample"][:1200],
                get_gui_result().get("highlight_terms", []),
            )
        )


def run_rag(conn, user_question, intent, force_new_search=False, rag_scope_mode="AUTO"):
    start = time.time()

    docs, search_query, searched, retrieval_stats = retrieve_docs(
        conn,
        user_question,
        force_new_search=force_new_search,
        rag_scope_mode=rag_scope_mode,
    )

    effective_scope_mode = retrieval_stats.get("RAGScopeMode", "GLOBAL")
    set_execution_mode(f"RAG_{effective_scope_mode}_FULLTEXT", "GPT_RAG")

    # Structured presentation data for GUI.
    # Retrieval itself is unchanged.
    set_gui_highlight_terms(
        normalize_rag_search_terms(search_query)
    )
    set_gui_retrieved_documents(docs)

    if not docs:
        print("No matching indexed document chunks found.")
        if effective_scope_mode == "HARD":
            print("Hard scope matched no usable semantic chunks. Scope was not silently broadened.")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["SearchQuery"] = search_query
            CURRENT_EXECUTION["NewSearch"] = searched
            CURRENT_EXECUTION["RetrievalStats"] = dict(retrieval_stats or {})
            CURRENT_EXECUTION["ExecutionStatus"] = "NoResults"
        return

    print("\nIntent:", intent)
    print("Thread:", THREAD["title"])
    print("RAG scope requested:", retrieval_stats.get("RAGScopeRequestedMode"))
    print("RAG scope effective:", retrieval_stats.get("RAGScopeMode"))
    print("RAG scope recommended:", retrieval_stats.get("RAGScopeRecommendedMode"))
    print("Scope:", json.dumps(retrieval_stats.get("RAGScope") or {}, ensure_ascii=False))
    if retrieval_stats.get("ScopedDocuments") is not None:
        print(f"Documents after scope filter: {retrieval_stats.get('ScopedDocuments')}")
    if retrieval_stats.get("ScopedChunks") is not None:
        print(f"Chunks after scope filter: {retrieval_stats.get('ScopedChunks')}")
    print("Semantic search query:", search_query)
    print("New search:", searched)

    context_chunks_per_doc = int(retrieval_stats.get("ContextChunksPerDoc") or 3)
    chunks_sent_est = sum(min(len(d.get("Chunks", [])), context_chunks_per_doc) for d in docs)
    print(f"Matched chunks: {retrieval_stats.get('MatchedChunks')}")
    print(f"Chunks retrieved: {retrieval_stats.get('ChunksRetrieved')} / limit {retrieval_stats.get('ChunkLimit')}")
    print(f"Unique documents matched: {retrieval_stats.get('UniqueDocumentsMatched')}")
    print(f"Retrieved documents: {len(docs)}")
    print(f"Chunks sent to GPT: {chunks_sent_est}")
    print("Thinking...\n")

    try:
        gpt_result = answer_with_gpt(user_question, docs, intent)
        answer = gpt_result["text"]
        elapsed_ms = int((time.time() - start) * 1000)

        print("=" * 100)
        print(answer)
        print("=" * 100)

        print("\nGPT usage:")
        print(f"- Prompt tokens: {gpt_result.get('prompt_tokens')}")
        print(f"- Completion tokens: {gpt_result.get('completion_tokens')}")
        print(f"- Total tokens: {gpt_result.get('total_tokens')}")
        print(f"- Prompt chars: {gpt_result.get('prompt_chars')}")

        THREAD["history"].append({
            "question": user_question,
            "answer": answer,
        })

        update_thread_summary(user_question, answer)
        log_question(
            conn, user_question, search_query, answer, docs, elapsed_ms,
            intent=intent,
            searched=searched,
            retrieval_stats=retrieval_stats,
            llm_usage=gpt_result,
            prompt_chars=gpt_result.get("prompt_chars"),
        )

        print("\nTop retrieved documents:")
        for d in docs[:10]:
            print(
                f"- {d['FileName']} | "
                f"RankingScore={d.get('RankingScore', d['BestScore']):.4f} | "
                f"BestScore={d['BestScore']:.4f}"
            )

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        log_question(
            conn, user_question, search_query, "", docs, elapsed_ms, error=str(e),
            intent=intent,
            searched=searched,
            retrieval_stats=retrieval_stats,
        )
        raise



# =====================================================
# Input helpers
# =====================================================

def read_user_question():
    """
    Read one user question from console.

    Normal single-line mode:
        Ask> 示例公司有哪些潜在客户？

    Multi-line mode:
        Ask> <<<
        请分析 Project Overview
        1. 总结
        2. Potential customers
        3. Action plan
        >>>

    Empty input in normal mode exits the program.
    """
    first_line = input("\nAsk> ")

    # Keep original behavior: empty line exits.
    if not first_line.strip():
        return ""

    # Multi-line mode starts with <<<.
    if first_line.strip() == "<<<":
        print("Multi-line mode. Finish with >>>")
        lines = []
        while True:
            line = input()
            if line.strip() == ">>>":
                break
            lines.append(line)
        return "\n".join(lines).strip()

    # Optional inline multi-line start:
    # Ask> <<< Please analyze this topic
    if first_line.lstrip().startswith("<<<"):
        remainder = first_line.lstrip()[3:].strip()
        print("Multi-line mode. Finish with >>>")
        lines = []
        if remainder:
            lines.append(remainder)
        while True:
            line = input()
            if line.strip() == ">>>":
                break
            lines.append(line)
        return "\n".join(lines).strip()

    return first_line.strip()


# =====================================================
# Main
# =====================================================

# Prefix route switches.
# RAG variants map to RAG_ANSWER and additionally control scope behavior.
FORCED_ROUTE_SWITCHES = {
    "RAG": ("RAG_ANSWER", "AUTO"),
    "RAGA": ("RAG_ANSWER", "AUTO"),
    "RAGH": ("RAG_ANSWER", "HARD"),
    "RAGS": ("RAG_ANSWER", "SOFT"),
    "RAGG": ("RAG_ANSWER", "GLOBAL"),
    "RAG-AUTO": ("RAG_ANSWER", "AUTO"),
    "RAG-HARD": ("RAG_ANSWER", "HARD"),
    "RAG-SOFT": ("RAG_ANSWER", "SOFT"),
    "RAG-GLOBAL": ("RAG_ANSWER", "GLOBAL"),
    "SEARCH": ("SEARCH_ONLY", None),
    "FILES": ("FILE_LIST", None),
    "CATALOG": ("CATALOG", None),
}


def parse_forced_route_switch(user_question):
    """
    Parse an optional leading route switch.

    RAG: / RAGA:       -> AUTO scope
    RAGH: / RAG-HARD: -> HARD scope
    RAGS: / RAG-SOFT: -> SOFT scope
    RAGG: / RAG-GLOBAL: -> GLOBAL scope

    Returns (intent, cleaned_question, switch_name, rag_scope_mode).
    """
    original = user_question or ""
    stripped = original.lstrip()

    m = re.match(r"^([A-Za-z_-]+)\s*:\s*(.*)$", stripped, flags=re.S)
    if not m:
        return None, original, None, None

    switch_name = m.group(1).upper()
    route_spec = FORCED_ROUTE_SWITCHES.get(switch_name)
    if not route_spec:
        return None, original, None, None

    intent, rag_scope_mode = route_spec
    cleaned = m.group(2).strip()
    return intent, cleaned, switch_name, rag_scope_mode


def detect_user_command(user_question):
    q = user_question.strip()
    if q.startswith("/"):
        return q.split(maxsplit=1)[0][:50]

    forced_intent, _, switch_name, _ = parse_forced_route_switch(user_question)
    if forced_intent and switch_name:
        return f"{switch_name}:"[:50]

    return None


def process_user_question(conn, user_question):
    """Execute one user request. Final logging is handled by main()."""
    reset_gui_result()
    qlower = user_question.lower()

    if qlower == "/help":
        set_execution_mode("HELP", "CONSOLE_TEXT")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "HELP"
        print_help()
        return

    if qlower == "/new":
        set_execution_mode("COMMAND", "CONSOLE_TEXT")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "NEW_THREAD"
        reset_thread()
        print("Started a new thread.")
        return

    if qlower == "/clear":
        set_execution_mode("COMMAND", "CONSOLE_TEXT")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "CLEAR_SEARCH_CACHE"

        THREAD["docs"] = []
        THREAD["last_search_query"] = ""
        THREAD["retrieval_stats"] = {}
        THREAD["current_doc_id"] = None
        THREAD["current_file_name"] = ""
        THREAD["current_full_path"] = ""
        THREAD["current_document_mode"] = False
        THREAD["last_rag_scope"] = {}
        THREAD["last_rag_scope_mode"] = "AUTO"
        THREAD["last_semantic_query"] = ""
        print("Cleared retrieval cache, RAG scope state and current-document binding. Conversation history was kept.")
        return

    if qlower == "/thread":
        set_execution_mode("COMMAND", "CONSOLE_TEXT")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "THREAD_STATUS"
        show_thread()
        return

    if qlower == "/stats":
        set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "FILE_COUNT"
        print_rows([get_file_count(conn)], user_question, title="File count summary")
        return

    if qlower == "/types":
        set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "TYPE_COUNT"
        print_rows(get_type_count(conn), user_question, title="File type statistics")
        return

    if qlower == "/folders":
        set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "FOLDER_CATALOG"
        print_rows(
            get_folder_catalog(conn, limit=120),
            user_question,
            title="Folder/category catalogue"
        )
        return

    if qlower.startswith("/files"):
        set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "FILE_LIST"
        keyword = user_question[6:].strip()
        print_rows(
            list_files(conn, keyword if keyword else None, limit=120),
            user_question,
            title="File list"
        )
        return

    if qlower == "/latest":
        set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "FILE_LIST"
            CURRENT_EXECUTION["RouterSearchQuery"] = ""
        print_rows(
            get_latest_modified_files(conn, limit=1),
            user_question,
            title="Latest modified file",
            max_rows=1
        )
        return

    if qlower.startswith("/summaries"):
        set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "FILE_LIST"
        keyword = user_question[10:].strip()
        print_rows(
            browse_summaries(conn, keyword if keyword else None, limit=80),
            user_question,
            title="Document summaries"
        )
        return

    if qlower.startswith("/search "):
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "SEARCH_ONLY"
        run_search_only(conn, user_question[8:].strip())
        return

    if qlower.startswith("/refresh "):
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "RAG_ANSWER"
            CURRENT_EXECUTION["ForceNewSearch"] = True
        run_rag(
            conn,
            user_question[9:].strip(),
            "RAG_ANSWER",
            force_new_search=True,
            rag_scope_mode="AUTO",
        )
        return

    if qlower.startswith("/catalog"):
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = "CATALOG"
        run_catalog_text(
            conn,
            user_question[8:].strip() or "Prepare a catalogue summary"
        )
        return

    # -------------------------------------------------
    # Forced route prefix switches
    # -------------------------------------------------
    # Examples:
    #   RAG: What special packaging requirements does Example Customer have?
    #   SEARCH: Example Customer packaging requirements
    #   FILES: Example Customer
    #   CATALOG: Europe
    #
    # If no recognized switch is present, execution continues into the
    # existing automatic router unchanged.
    forced_intent, forced_question, switch_name, forced_rag_scope_mode = parse_forced_route_switch(user_question)

    if forced_intent is not None:
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["RouterIntent"] = forced_intent
            CURRENT_EXECUTION["RouterJSON"] = {
                "intent": forced_intent,
                "search_query": forced_question,
                "topic_title": f"Forced {switch_name}",
                "force_new_search": True,
                "forced_by_prefix": True,
                "switch": switch_name,
                "rag_scope_mode": forced_rag_scope_mode,
            }
            CURRENT_EXECUTION["RouterSearchQuery"] = forced_question
            CURRENT_EXECUTION["RouterTopicTitle"] = f"Forced {switch_name}"
            CURRENT_EXECUTION["ForceNewSearch"] = True

        if forced_rag_scope_mode:
            print(f"\nForced route switch: {switch_name}: -> {forced_intent} | scope={forced_rag_scope_mode}")
        else:
            print(f"\nForced route switch: {switch_name}: -> {forced_intent}")

        if not forced_question:
            set_execution_mode("COMMAND", "CONSOLE_TEXT")
            print(f"{switch_name}: 后面没有问题或搜索内容。")
            if CURRENT_EXECUTION is not None:
                CURRENT_EXECUTION["ExecutionStatus"] = "EmptyForcedRouteQuery"
            return

        if forced_intent == "RAG_ANSWER":
            # A forced RAG request always performs a fresh retrieval so it cannot
            # accidentally reuse cached documents from the previous topic.
            run_rag(
                conn,
                forced_question,
                "RAG_ANSWER",
                force_new_search=True,
                rag_scope_mode=forced_rag_scope_mode or "AUTO",
            )
            return

        if forced_intent == "SEARCH_ONLY":
            run_search_only(conn, forced_question)
            return

        if forced_intent == "FILE_LIST":
            set_execution_mode("METADATA_SQL", "GPT_MARKDOWN")
            rows = list_files(conn, forced_question, limit=120)
            print_rows(rows, forced_question, title="File list")
            return

        if forced_intent == "CATALOG":
            run_catalog_text(conn, forced_question)
            return


    explicit_filename = extract_named_filename(user_question)
    if not explicit_filename:
        explicit_filename = extract_filename_stem_candidate(user_question)

    if explicit_filename:
        if run_single_document(conn, user_question, explicit_filename=explicit_filename):
            return

    if THREAD.get("current_document_mode") and is_document_followup(user_question):
        if run_single_document(conn, user_question):
            return

    route = route_question(user_question)
    intent = route["intent"]

    print("\nRouter intent:", intent)

    if intent == "HELP":
        set_execution_mode("HELP", "CONSOLE_TEXT")
        print_help()
        return

    if intent == "NEW_THREAD":
        set_execution_mode("COMMAND", "CONSOLE_TEXT")
        reset_thread()
        print("Started a new thread.")
        return

    if intent == "THREAD_STATUS":
        set_execution_mode("COMMAND", "CONSOLE_TEXT")
        show_thread()
        return

    if intent == "SINGLE_DOCUMENT_ANALYSIS":
        # Strict rule: a single-document intent must never fall back to full-text RAG.
        candidate = extract_named_filename(user_question) or extract_filename_stem_candidate(user_question)
        if candidate:
            run_single_document(conn, user_question, explicit_filename=candidate)
            return
        if run_single_document(conn, user_question):
            return

        print("已识别为单文档分析，但当前没有可绑定的唯一文件。请在问题中写明文件名。")
        if CURRENT_EXECUTION is not None:
            CURRENT_EXECUTION["ExecutionStatus"] = "NoDocumentBound"
            CURRENT_EXECUTION["SearchMode"] = "SINGLE_DOCUMENT_SQL"
            CURRENT_EXECUTION["PresentationMode"] = "CONSOLE_TEXT"
        return

    if run_metadata(conn, user_question, intent, route):
        return

    if intent == "SEARCH_ONLY":
        run_search_only(conn, user_question)
        return

    if intent == "CATALOG":
        run_catalog_text(conn, user_question)
        return

    if intent == "RAG_ANSWER":
        run_rag(
            conn,
            user_question,
            "RAG_ANSWER",
            force_new_search=route.get("force_new_search", False),
            rag_scope_mode="AUTO",
        )
        return

    run_rag(
        conn,
        user_question,
        "RAG_ANSWER",
        force_new_search=False,
        rag_scope_mode="AUTO",
    )


def main():
    conn = psycopg.connect(**DB_CONFIG)

    print("\n====================================")
    print(" Generic Enterprise KB RAG Agent")
    print(" PostgreSQL 17 + Router + SQL + Scoped RAG + Unified AgentExecutionLog")
    print("====================================")
    print("Type /help for commands. Use <<< then >>> for multi-line ask.")

    try:
        while True:
            user_question = read_user_question()

            if not user_question:
                break

            begin_execution(
                user_question,
                user_command=detect_user_command(user_question)
            )

            capture = TeeOutput(sys.stdout)
            original_stdout = sys.stdout
            sys.stdout = capture

            try:
                process_user_question(conn, user_question)

            except Exception as exc:
                if CURRENT_EXECUTION is not None:
                    CURRENT_EXECUTION["ExecutionStatus"] = "Failed"
                    CURRENT_EXECUTION["ErrorStage"] = (
                        CURRENT_EXECUTION.get("ErrorStage") or "AGENT"
                    )
                    CURRENT_EXECUTION["ErrorType"] = type(exc).__name__
                    CURRENT_EXECUTION["ErrorMessage"] = str(exc)
                    CURRENT_EXECUTION["ErrorTraceback"] = traceback.format_exc()

                print("\nAgent request failed:")
                print(traceback.format_exc())

            finally:
                sys.stdout = original_stdout

                if CURRENT_EXECUTION is not None:
                    output = capture.getvalue().strip()
                    if not CURRENT_EXECUTION.get("FinalAnswer"):
                        CURRENT_EXECUTION["FinalAnswer"] = output

                    # If a query returned no rows and no harder failure occurred,
                    # classify it as NoResults.
                    if (
                        CURRENT_EXECUTION.get("ExecutionStatus") == "Success"
                        and CURRENT_EXECUTION.get("RowsReturned") == 0
                    ):
                        CURRENT_EXECUTION["ExecutionStatus"] = "NoResults"

                write_execution_log(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
