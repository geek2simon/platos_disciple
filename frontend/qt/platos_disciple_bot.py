# -*- coding: utf-8 -*-
"""
Plato's Disciple - Local Knowledge Base AI Agent
Qt GUI thin shell over core/platos_disciple_core.py

Expected layout:
<project root>/
    _assets/
        platos_disciple_icon_256.png
    core/
        platos_disciple_core.py
    frontend/
        qt/
            platos_disciple_ai_agent.py

No drive letter is hard-coded.
All KB/RAG/PostgreSQL/LLM logic stays in platos_disciple_core.py.
"""

from __future__ import annotations

import html
import io
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# ============================================================
# CONFIG
# ============================================================

APP_TITLE = "Plato's Disciple - Local Knowledge Bot"
HEADER_LABEL = "Plato's Disciple"

ASSETS_FOLDER_NAME = "_assets"
APP_ICON_NAME = "platos_disciple_icon_256.png"

WINDOW_X = 80
WINDOW_Y = 80
WINDOW_WIDTH = 620
WINDOW_HEIGHT = 800
WINDOW_MIN_WIDTH = 500
WINDOW_MIN_HEIGHT = 620

CORE_FOLDER_NAME = "core"
CORE_MODULE_NAME = "platos_disciple_core"

TAB_RAG_LABEL = "AI Conversation"
TAB_CLASSIC_LABEL = "Classic Search"

RAG_INPUT_PLACEHOLDER = "Ask your local knowledge base..."
CLASSIC_INPUT_PLACEHOLDER = "Search local documents..."

READY_TEXT = "Ready"
THINKING_WORD = "Thinking"

# ============================================================
# DYNAMIC DISCOVERY OF CORE FOLDER
# ============================================================

GUI_FILE = Path(__file__).resolve()
GUI_DIR = GUI_FILE.parent
FRONTEND_DIR = GUI_DIR.parent
APP_ROOT = FRONTEND_DIR.parent
CORE_DIR = APP_ROOT / CORE_FOLDER_NAME
CORE_FILE = CORE_DIR / f"{CORE_MODULE_NAME}.py"
ASSETS_DIR = APP_ROOT / ASSETS_FOLDER_NAME
APP_ICON_FILE = ASSETS_DIR / APP_ICON_NAME

if not CORE_DIR.exists():
    raise RuntimeError(
        f"Plato\'s Disciple core folder not found:\n{CORE_DIR}\n\n"
        "Expected core to be a sibling of the frontend folder."
    )

if not CORE_FILE.exists():
    raise RuntimeError(f"Plato\'s Disciple core file not found:\n{CORE_FILE}")

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

CONFIG_DIR = APP_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config_general.py"

# Load core only after the Qt window exists, so configuration/startup errors
# can be shown inside the GUI instead of terminating in the console.
core = None


def validate_core() -> None:
    required = ["psycopg", "DB_CONFIG", "process_user_question"]
    missing = [name for name in required if not hasattr(core, name)]
    if missing:
        raise RuntimeError(
            f"{CORE_MODULE_NAME}.py is missing required item(s):\n"
            + "\n".join(f"- {name}" for name in missing)
        )


def load_core():
    global core
    import importlib
    core = importlib.import_module(CORE_MODULE_NAME)
    validate_core()


def validate_azure_configuration() -> None:
    """Check the required Azure OpenAI configuration values."""
    values = [
        str(getattr(core, name, "") or "").strip()
        for name in ("ENDPOINT_URL", "API_KEY", "DEPLOYMENT_NAME")
    ]
    placeholders = ("YOUR_OWN", "REPLACE_ME", "CHANGE_ME", "EXAMPLE")
    if (
        not all(values)
        or not values[0].lower().startswith("https://")
        or any(marker in value.upper() for value in values for marker in placeholders)
    ):
        raise RuntimeError("Azure OpenAI configuration is invalid")


def validate_postgresql_connection() -> None:
    """Verify the local PostgreSQL login with a short timeout."""
    db_config = dict(core.DB_CONFIG)
    db_config.setdefault("connect_timeout", 5)
    with core.psycopg.connect(**db_config):
        pass


def friendly_error(error) -> tuple[str, str]:
    """Convert common config failures to concise user instructions."""
    text = str(error or "Unknown error")
    detail = text.strip().splitlines()[-1]
    lowered = text.lower()

    if any(x in lowered for x in (
        "psycopg", "postgres", "authentication failed", "connection refused",
        "could not connect", "could not translate host", "database does not exist",
    )):
        return (
            "PostgreSQL Connection Failed",
            "In config/config_general.py, confirm DB_USER is kb_agent and "
            "DB_PASSWORD is the legitimate local password assigned to kb_agent. "
            "Also verify DB_HOST, DB_PORT, DB_NAME, and the PostgreSQL service.\n\n"
            f"Technical detail: {detail}",
        )

    if any(x in lowered for x in (
        "azure", "openai", "api key", "api_key", "endpoint_url", "base_url",
        "deployment_name", "authenticationerror", "apiconnectionerror",
        "permissiondeniederror", "notfounderror", "401", "403", "404",
    )):
        return (
            "Azure OpenAI Configuration Failed",
            "In config/config_general.py, check ENDPOINT_URL, API_KEY, and "
            "DEPLOYMENT_NAME. The URL and key must be legitimate values obtained "
            "from your own Azure account.\n\n"
            f"Technical detail: {detail}",
        )

    return ("Plato's Disciple Error", f"The request could not be completed.\n\n{detail}")


# ============================================================
# STYLE
# ============================================================

APP_STYLE = """
QMainWindow { background: #eef2f6; }
QFrame#MainCard {
    background: white;
    border: 1px solid #cbd5e1;
    border-radius: 10px;
}
QLabel#Header {
    font-size: 20px;
    font-weight: 700;
    color: #172033;
    padding: 4px 2px 8px 2px;
}
QLabel#SectionLabel {
    font-size: 12px;
    font-weight: 600;
    color: #334155;
}
QTabWidget::pane {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    background: white;
}
QTabBar::tab {
    padding: 8px 16px;
    color: #475569;
    background: #f1f5f9;
    border: 1px solid #cbd5e1;
    border-bottom: none;
}
QTabBar::tab:selected {
    color: #1d4ed8;
    background: white;
    font-weight: 600;
    border-top: 2px solid #2563eb;
}
QTextBrowser {
    border: 1px solid #22a06b;
    border-radius: 6px;
    background: #f8fafc;
    color: #1e293b;
    padding: 6px;
}
QLineEdit {
    border: 1px solid #2563eb;
    border-radius: 6px;
    padding: 9px;
    background: white;
    color: #172033;
}
QPushButton {
    border: 1px solid #b8c3d1;
    border-radius: 6px;
    padding: 8px 14px;
    background: #f8fafc;
    color: #334155;
}
QPushButton:hover { background: #e8eef5; }
QPushButton:pressed { background: #dbe4ee; }
QPushButton#PrimaryButton {
    color: white;
    background: #2563eb;
    border-color: #2563eb;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover { background: #1d4ed8; }
QPushButton#PrimaryButton:pressed { background: #1e40af; }
QPushButton:disabled { color: #9aa0a8; }
QRadioButton { padding: 2px 5px 2px 0px; }
"""

# ============================================================
# CORE ADAPTER
# ============================================================


def call_core(question: str, command_prefix: str) -> str:
    """Thin adapter only. All actual search/RAG logic remains in platos_disciple_core.py."""
    full_question = f"{command_prefix}: {question}"
    conn = core.psycopg.connect(**core.DB_CONFIG)

    capture = io.StringIO()
    old_stdout = sys.stdout

    try:
        if hasattr(core, "begin_execution"):
            user_command = None
            if hasattr(core, "detect_user_command"):
                try:
                    user_command = core.detect_user_command(full_question)
                except Exception:
                    user_command = None

            try:
                core.begin_execution(full_question, user_command=user_command)
            except TypeError:
                core.begin_execution(full_question)

        sys.stdout = capture
        core.process_user_question(conn, full_question)
        sys.stdout = old_stdout

        output = capture.getvalue().strip()

        current = getattr(core, "CURRENT_EXECUTION", None)
        if isinstance(current, dict) and not current.get("FinalAnswer"):
            current["FinalAnswer"] = output

        if hasattr(core, "write_execution_log"):
            try:
                core.write_execution_log(conn)
            except Exception:
                pass

        gui_result = {}
        if hasattr(core, "get_gui_result"):
            try:
                gui_result = core.get_gui_result() or {}
            except Exception:
                gui_result = {}

        return {
            "text": output or "(No output returned.)",
            "highlight_terms": list(gui_result.get("highlight_terms") or []),
            "retrieved_documents": list(gui_result.get("retrieved_documents") or []),
        }

    except Exception as exc:
        sys.stdout = old_stdout
        try:
            current = getattr(core, "CURRENT_EXECUTION", None)
            if isinstance(current, dict):
                current["ExecutionStatus"] = "Failed"
                current["ErrorStage"] = current.get("ErrorStage") or "GUI"
                current["ErrorType"] = type(exc).__name__
                current["ErrorMessage"] = str(exc)
                current["ErrorTraceback"] = traceback.format_exc()

            if hasattr(core, "write_execution_log"):
                core.write_execution_log(conn)
        except Exception:
            pass
        raise

    finally:
        try:
            conn.close()
        except Exception:
            pass

# Classic Search visible paragraph/snippet length.
# Set 0 for unlimited.
CLASSIC_PARAGRAPH_CHARS = 200

# ============================================================
# RAG GUI PRESENTATION FILTER
# ============================================================

def clean_rag_output(raw_text: str) -> str:
    """Hide backend diagnostics in GUI; retain answer and document list."""
    skip_prefixes = (
        "Forced route switch:", "Intent:", "Thread:",
        "RAG scope requested:", "RAG scope effective:",
        "RAG scope recommended:", "Scope:",
        "Documents after scope filter:", "Chunks after scope filter:",
        "Semantic search query:", "New search:", "Matched chunks:",
        "Chunks retrieved:", "Unique documents matched:",
        "Retrieved documents:", "Chunks sent to GPT:",
        "GPT usage:", "- Prompt tokens:", "- Completion tokens:",
        "- Total tokens:", "- Prompt chars:",
    )
    kept = []
    for line in (raw_text or "").splitlines():
        s = line.strip()
        if s.startswith(skip_prefixes):
            continue
        if s and set(s) <= {"="}:
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


# ============================================================
# DISPLAY HELPERS ONLY
# ============================================================

WINDOWS_PATH_RE = re.compile(
    r'(?P<path>[A-Za-z]:\\[^\r\n<>\"]+?\.(?:pdf|docx?|xlsx?|xlsm|pptx?|txt|csv|html?|mhtml|msg|eml))',
    re.IGNORECASE,
)

UNC_PATH_RE = re.compile(
    r'(?P<path>\\\\[^\r\n<>\"]+?\.(?:pdf|docx?|xlsx?|xlsm|pptx?|txt|csv|html?|mhtml|msg|eml))',
    re.IGNORECASE,
)


def get_query_terms(text: str) -> list[str]:
    terms = re.findall(r"[\w\u3400-\u9fff.-]+", text or "", flags=re.UNICODE)
    result, seen = [], set()
    for term in terms:
        term = term.strip(" .-_")
        if not term:
            continue
        key = term.lower()
        if key not in seen:
            seen.add(key)
            result.append(term)
    return result[:15]


def convert_paths_to_links(raw_text: str, safe_text: str) -> str:
    paths = []
    for pattern in (WINDOWS_PATH_RE, UNC_PATH_RE):
        for match in pattern.finditer(raw_text or ""):
            path = match.group("path").strip()
            if path not in paths:
                paths.append(path)

    for path in sorted(paths, key=len, reverse=True):
        escaped_path = html.escape(path)
        url = QUrl.fromLocalFile(path).toString()
        link = f"<a href='{html.escape(url, quote=True)}'>{escaped_path}</a>"
        safe_text = safe_text.replace(escaped_path, link)

    return safe_text


def highlight_terms_html(safe_text: str, terms) -> str:
    """
    GUI-only rendering. Core decides WHAT terms are relevant;
    Qt decides HOW they look.
    """
    clean_terms = []
    seen = set()

    for term in terms or []:
        value = str(term or "").strip()
        if not value:
            continue

        key = value.lower()
        if key in seen:
            continue

        seen.add(key)
        clean_terms.append(value)

    for term in sorted(clean_terms, key=len, reverse=True):
        escaped_term = html.escape(term)

        safe_text = re.sub(
            re.escape(escaped_term),
            lambda m: (
                "<span style='background-color:#fff176;"
                "font-weight:600;'>"
                f"{m.group(0)}"
                "</span>"
            ),
            safe_text,
            flags=re.IGNORECASE,
        )

    return safe_text

def make_match_snippet(text: str, terms, max_chars: int) -> str:
    """
    Build a Google-style snippet centered around the first actual matched term.

    If no matched term is found, fall back to the beginning of the paragraph.
    """
    value = str(text or "")

    if max_chars <= 0 or len(value) <= max_chars:
        return value

    lower_value = value.lower()
    hit_positions = []

    for term in terms or []:
        term = str(term or "").strip()

        if not term:
            continue

        pos = lower_value.find(term.lower())

        if pos >= 0:
            hit_positions.append(pos)

    if not hit_positions:
        return value[:max_chars].rstrip() + "…"

    hit = min(hit_positions)

    # Put the hit roughly one-third into the visible snippet.
    before_chars = max_chars // 3

    start = max(0, hit - before_chars)
    end = min(len(value), start + max_chars)

    # If the end hit the document boundary, shift left to use the full window.
    if end == len(value):
        start = max(0, end - max_chars)

    snippet = value[start:end].strip()

    if start > 0:
        snippet = "…" + snippet

    if end < len(value):
        snippet += "…"

    return snippet


def render_classic_html(raw_text: str, highlight_terms) -> str:
    """Presentation only: compact snippets + yellow highlight + file links."""
    display_lines = []
    for line in (raw_text or "").splitlines():
        s = line.strip()
        is_path = bool(WINDOWS_PATH_RE.search(line) or UNC_PATH_RE.search(line))
        is_metadata = s.startswith("[") or s.lower().startswith(
            ("file", "path", "score", "doc")
        )
        if (
            CLASSIC_PARAGRAPH_CHARS > 0
            and len(line) > CLASSIC_PARAGRAPH_CHARS
            and not is_path
            and not is_metadata
        ):
            line = make_match_snippet(
                line,
                highlight_terms,
                CLASSIC_PARAGRAPH_CHARS,
            )
        display_lines.append(line)

    display_text = "\n".join(display_lines)
    safe = html.escape(display_text)
    safe = convert_paths_to_links(display_text, safe)
    safe = highlight_terms_html(safe, highlight_terms)
    safe = safe.replace("\n", "<br>")
    return f"<div style='font-size:13px;line-height:1.45;'>{safe}</div>"

def format_clock_time(dt=None) -> str:
    """Compact local time shown beside questions/answers."""
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def linkify_document_names(safe_text: str, documents) -> str:
    """
    Turn source filenames mentioned inside an already HTML-escaped RAG answer
    into clickable local-file links using core-provided FullPath values.
    """
    result = safe_text
    docs = documents or []

    # Longest names first to avoid partial-name collisions.
    docs = sorted(
        docs,
        key=lambda d: len(str(d.get("FileName") or "")),
        reverse=True,
    )

    for doc in docs:
        name = str(doc.get("FileName") or "").strip()
        path = str(doc.get("FullPath") or "").strip()

        if not name or not path:
            continue

        escaped_name = html.escape(name)
        url = QUrl.fromLocalFile(path).toString()

        link = (
            f"<a href='{html.escape(url, quote=True)}'>"
            f"{escaped_name}</a>"
        )

        # The answer was escaped before this function, so matching the escaped
        # filename is safe. Case-insensitive replacement handles filename case.
        result = re.sub(
            re.escape(escaped_name),
            lambda _m, link=link: link,
            result,
            flags=re.IGNORECASE,
        )

    return result


def conversation_separator_html() -> str:
    return (
        "<div style='margin:12px 0 10px 0;"
        "color:#9aa0a8;font-family:monospace;'>"
        "============================================================"
        "</div>"
    )


def render_retrieved_documents_html(documents, title="Retrieved documents") -> str:
    """Render core-provided document paths as clickable local-file links."""
    docs = documents or []
    if not docs:
        return ""

    parts = [
        "<div style='margin-top:12px;padding-top:8px;"
        "border-top:1px solid #d9dde3;'>",
        f"<div style='font-size:12px;font-weight:600;margin-bottom:5px;'>{html.escape(title)}</div>",
    ]

    for i, doc in enumerate(docs, 1):
        name = str(doc.get("FileName") or "(unnamed file)")
        path = str(doc.get("FullPath") or "")

        if path:
            url = QUrl.fromLocalFile(path).toString()
            link = (
                f"<a href='{html.escape(url, quote=True)}'>"
                f"{html.escape(name)}</a>"
            )
        else:
            link = html.escape(name)

        parts.append(
            "<div style='margin:3px 0;'>"
            f"{i}. {link}"
            "</div>"
        )

    parts.append("</div>")
    return "".join(parts)


# ============================================================
# WORKER
# ============================================================


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class StartupSignals(QObject):
    progress = Signal(str)
    ready = Signal()
    error = Signal(object)


class StartupWorker(QRunnable):
    """Load and validate the core without blocking the Qt event loop."""

    def __init__(self):
        super().__init__()
        self.signals = StartupSignals()

    def run(self):
        try:
            self.signals.progress.emit("Checking local configuration...")
            if not CONFIG_FILE.exists():
                raise FileNotFoundError(
                    "The required configuration file was not found:\n"
                    f"    {CONFIG_FILE}\n\n"
                    "Please create config/config_general.py with your legitimate "
                    "local PostgreSQL kb_agent credentials and your own legitimate "
                    "Azure OpenAI API URL, key, and deployment name."
                )

            self.signals.progress.emit("Loading Plato's Disciple core module...")
            load_core()

            self.signals.progress.emit("Validating Azure OpenAI configuration...")
            validate_azure_configuration()

            self.signals.progress.emit("Connecting to PostgreSQL knowledge base...")
            validate_postgresql_connection()

            self.signals.ready.emit()
        except Exception as exc:
            self.signals.error.emit(exc)


class CoreWorker(QRunnable):
    def __init__(self, question: str, prefix: str):
        super().__init__()
        self.question = question
        self.prefix = prefix
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(call_core(self.question, self.prefix))
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()

# ============================================================
# MAIN WINDOW
# ============================================================


class PlatosDiscipleMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.busy = False
        self.initialized = False
        self.startup_worker = None
        self.current_startup_step = "Initializing Plato's Disciple..."
        self.thinking_seconds = 0
        self.thinking_phase = 0
        self.current_classic_query = ""

        self.rag_started_perf = None
        self.rag_asked_at = None
        self.classic_started_perf = None
        self.classic_asked_at = None

        self.setWindowTitle(APP_TITLE)
        self.setGeometry(WINDOW_X, WINDOW_Y, WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        self.thinking_timer = QTimer(self)
        self.thinking_timer.setInterval(500)
        self.thinking_timer.timeout.connect(self._tick_thinking)

        self._build_ui()
        self._set_conversation_enabled(False)
        self._show_initializing_message()
        QTimer.singleShot(50, self._startup_check)

    def _show_initializing_message(self):
        self._set_startup_status(self.current_startup_step)

    def _set_startup_status(
        self,
        message: str,
        ready: bool = False,
        error_text: str = "",
    ):
        color = "#287a3f" if ready else "#46515e"
        error_html = ""
        if error_text:
            error_html = (
                " <span style='color:#c62828;font-weight:700;'>"
                f"(error: {html.escape(error_text)})"
                "</span>"
            )
        self.rag_browser.setHtml(
            "<div style='margin:12px;font-size:13px;"
            f"font-weight:700;color:{color};'>"
            f"{html.escape(message)}"
            f"{error_html}"
            "</div>"
        )

    def _append_startup_progress(self, message: str):
        self.current_startup_step = message
        self.status_label.setText(message)
        self._set_startup_status(message)

    def _set_conversation_enabled(self, enabled: bool):
        """Enable only interactive conversation controls; keep status visible."""
        self.rag_input.setEnabled(enabled)
        self.rag_send.setEnabled(enabled)
        self.rag_clear.setEnabled(enabled)
        self.classic_input.setEnabled(enabled)
        self.classic_send.setEnabled(enabled)
        self.classic_clear.setEnabled(enabled)

        for radio in self.rag_radios.values():
            radio.setEnabled(enabled)
        for radio in self.classic_radios.values():
            radio.setEnabled(enabled)

    def _startup_check(self):
        self.startup_worker = StartupWorker()
        self.startup_worker.signals.progress.connect(self._append_startup_progress)
        self.startup_worker.signals.ready.connect(self._startup_ready)
        self.startup_worker.signals.error.connect(self._startup_failed)
        self.pool.start(self.startup_worker)

    def _startup_ready(self):
        self.initialized = True
        self._set_conversation_enabled(True)
        self._set_startup_status(
            "Ready — You can now start a conversation.",
            ready=True,
        )
        self.status_label.setText(READY_TEXT)
        self.rag_input.setFocus()
        self.startup_worker = None

    def _startup_failed(self, exc):
        self.initialized = False
        self._set_conversation_enabled(False)

        if isinstance(exc, FileNotFoundError):
            error_text = f"configuration file not found: {CONFIG_FILE}"
            status = "Configuration required"
        else:
            detail = str(exc or "Unknown error").strip().splitlines()[-1]
            error_text = detail
            status = "Startup error"

        self._set_startup_status(
            self.current_startup_step,
            error_text=error_text,
        )
        self.status_label.setText(status)
        self.startup_worker = None

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 10, 10, 10)

        card = QFrame()
        card.setObjectName("MainCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        header = QLabel(HEADER_LABEL)
        header.setObjectName("Header")
        layout.addWidget(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._build_rag_tab()
        self._build_classic_tab()

        self.status_label = QLabel(READY_TEXT)
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.setStyleSheet("color:#68707b;padding-right:3px;")
        layout.addWidget(self.status_label)

    def _build_rag_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        section = QLabel("RAG mode")
        section.setObjectName("SectionLabel")
        layout.addWidget(section)

        self.rag_group = QButtonGroup(self)
        self.rag_radios = {}

        rag_options = [
            ("RAGH", "Focused RAG — strict metadata scope"),
            ("RAGS", "Flexible RAG — metadata relevance boost"),
            ("RAGG", "Global RAG — search entire knowledge base"),
            ("RAG", "Auto RAG — let agent choose scope"),
        ]

        for prefix, label_text in rag_options:
            radio = QRadioButton(label_text)
            self.rag_group.addButton(radio)
            self.rag_radios[prefix] = radio
            layout.addWidget(radio)

        self.rag_radios["RAGH"].setChecked(True)

        self.rag_browser = QTextBrowser()
        self.rag_browser.setOpenLinks(False)
        self.rag_browser.setOpenExternalLinks(False)
        self.rag_browser.anchorClicked.connect(self.open_local_file)
        self.rag_browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.rag_browser, 1)

        row = QHBoxLayout()
        self.rag_input = QLineEdit()
        self.rag_input.setPlaceholderText(RAG_INPUT_PLACEHOLDER)
        self.rag_input.returnPressed.connect(self.submit_rag)
        row.addWidget(self.rag_input, 1)

        self.rag_send = QPushButton("Ask")
        self.rag_send.setObjectName("PrimaryButton")
        self.rag_send.clicked.connect(self.submit_rag)
        row.addWidget(self.rag_send)

        self.rag_clear = QPushButton("Clear")
        self.rag_clear.clicked.connect(self.rag_browser.clear)
        row.addWidget(self.rag_clear)

        layout.addLayout(row)
        self.tabs.addTab(tab, TAB_RAG_LABEL)

    def _build_classic_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        section = QLabel("Classic non-RAG mode")
        section.setObjectName("SectionLabel")
        layout.addWidget(section)

        self.classic_group = QButtonGroup(self)
        self.classic_radios = {}

        classic_options = [
            ("SEARCH", "Document Search — classic ranked search"),
            ("FILES", "File Search — file name / path / metadata"),
            ("CATALOG", "Catalog — browse knowledge-base structure"),
        ]

        for prefix, label_text in classic_options:
            radio = QRadioButton(label_text)
            self.classic_group.addButton(radio)
            self.classic_radios[prefix] = radio
            layout.addWidget(radio)

        self.classic_radios["SEARCH"].setChecked(True)

        self.classic_browser = QTextBrowser()
        self.classic_browser.setOpenLinks(False)
        self.classic_browser.anchorClicked.connect(self.open_local_file)
        layout.addWidget(self.classic_browser, 1)

        row = QHBoxLayout()
        self.classic_input = QLineEdit()
        self.classic_input.setPlaceholderText(CLASSIC_INPUT_PLACEHOLDER)
        self.classic_input.returnPressed.connect(self.submit_classic)
        row.addWidget(self.classic_input, 1)

        self.classic_send = QPushButton("Search")
        self.classic_send.setObjectName("PrimaryButton")
        self.classic_send.clicked.connect(self.submit_classic)
        row.addWidget(self.classic_send)

        self.classic_clear = QPushButton("Clear")
        self.classic_clear.clicked.connect(self.classic_browser.clear)
        row.addWidget(self.classic_clear)

        layout.addLayout(row)
        self.tabs.addTab(tab, TAB_CLASSIC_LABEL)

    def selected_rag_prefix(self) -> str:
        for prefix, radio in self.rag_radios.items():
            if radio.isChecked():
                return prefix
        return "RAGH"

    def selected_classic_prefix(self) -> str:
        for prefix, radio in self.classic_radios.items():
            if radio.isChecked():
                return prefix
        return "SEARCH"

    def start_thinking(self):
        self.busy = True
        self.thinking_seconds = 0
        self.thinking_phase = 0
        self.rag_send.setEnabled(False)
        self.classic_send.setEnabled(False)
        self.rag_input.setEnabled(False)
        self.classic_input.setEnabled(False)
        self.thinking_timer.start()
        self._tick_thinking()

    def stop_thinking(self):
        self.thinking_timer.stop()
        self.status_label.setText(READY_TEXT)
        self.status_label.setStyleSheet("color:#68707b;padding-right:3px;")
        self.busy = False
        self.rag_send.setEnabled(True)
        self.classic_send.setEnabled(True)
        self.rag_input.setEnabled(True)
        self.classic_input.setEnabled(True)

    def _tick_thinking(self):
        self.thinking_phase += 1
        if self.thinking_phase % 2 == 0:
            self.thinking_seconds += 1

        dots = "." * ((self.thinking_phase % 3) + 1)
        color = "#303842" if self.thinking_phase % 2 else "#a5abb3"
        self.status_label.setStyleSheet(
            f"color:{color};font-weight:600;padding-right:3px;"
        )
        self.status_label.setText(
            f"{THINKING_WORD}{dots} {self.thinking_seconds}s"
        )

    def submit_rag(self):
        if not self.initialized or self.busy:
            return

        question = self.rag_input.text().strip()
        if not question:
            return

        prefix = self.selected_rag_prefix()
        self.rag_input.clear()

        self.rag_asked_at = datetime.now()
        self.rag_started_perf = time.perf_counter()

        self.rag_browser.append(
            conversation_separator_html()
            + "<div style='margin:8px 0 12px 0;'>"
            f"<div style='font-size:11px;color:#7b828c;'>"
            f"You · {html.escape(prefix)} · {html.escape(format_clock_time(self.rag_asked_at))}"
            "</div>"
            f"<div style='font-size:14px;'><b>{html.escape(question)}</b></div>"
            "</div>"
        )

        self.start_thinking()

        worker = CoreWorker(question, prefix)
        worker.signals.result.connect(self.show_rag_result)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(self.stop_thinking)
        self.pool.start(worker)

    def show_rag_result(self, payload):
        raw_text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        documents = payload.get("retrieved_documents", []) if isinstance(payload, dict) else []

        completed_at = datetime.now()
        elapsed_seconds = (
            time.perf_counter() - self.rag_started_perf
            if self.rag_started_perf is not None
            else 0.0
        )

        display_text = clean_rag_output(raw_text)

        # Escape first, then link source filenames using core-provided FullPath.
        safe = html.escape(display_text)
        safe = linkify_document_names(safe, documents)
        safe = safe.replace("\n", "<br>")

        docs_html = render_retrieved_documents_html(
            documents[:10],
            title="Retrieved documents",
        )

        self.rag_browser.append(
            "<div style='margin:6px 0 16px 0;'>"
            "<div style='font-size:11px;color:#7b828c;'>"
            f"Plato\'s Disciple · {html.escape(format_clock_time(completed_at))}"
            f" · {elapsed_seconds:.1f}s"
            "</div>"
            f"<div style='font-size:14px;line-height:1.45;'>{safe}</div>"
            f"{docs_html}"
            "</div>"
        )

        bar = self.rag_browser.verticalScrollBar()
        bar.setValue(bar.maximum())

    def submit_classic(self):
        if not self.initialized or self.busy:
            return

        question = self.classic_input.text().strip()
        if not question:
            return

        prefix = self.selected_classic_prefix()
        self.current_classic_query = question
        self.classic_asked_at = datetime.now()
        self.classic_started_perf = time.perf_counter()

        self.start_thinking()

        worker = CoreWorker(question, prefix)
        worker.signals.result.connect(self.show_classic_result)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(self.stop_thinking)
        self.pool.start(worker)

    def show_classic_result(self, payload):
        if isinstance(payload, dict):
            raw_text = payload.get("text", "")
            highlight_terms = payload.get("highlight_terms", [])
            documents = payload.get("retrieved_documents", [])
        else:
            raw_text = str(payload)
            highlight_terms = [self.current_classic_query]
            documents = []

        completed_at = datetime.now()
        elapsed_seconds = (
            time.perf_counter() - self.classic_started_perf
            if self.classic_started_perf is not None
            else 0.0
        )

        result_html = render_classic_html(
            raw_text,
            highlight_terms,
        )

        docs_html = render_retrieved_documents_html(
            documents[:20],
            title="Source files",
        )

        asked_at_text = format_clock_time(self.classic_asked_at)
        completed_at_text = format_clock_time(completed_at)

        header_html = (
            conversation_separator_html()
            + "<div style='margin:4px 0 8px 0;'>"
            f"<div style='font-size:11px;color:#7b828c;'>"
            f"You · {html.escape(asked_at_text)}"
            "</div>"
            f"<div style='font-size:14px;'><b>"
            f"{html.escape(self.current_classic_query)}"
            "</b></div>"
            f"<div style='font-size:11px;color:#7b828c;margin-top:5px;'>"
            f"Plato\'s Disciple · {html.escape(completed_at_text)} · {elapsed_seconds:.1f}s"
            "</div>"
            "</div>"
        )

        self.classic_browser.setHtml(
            header_html + result_html + docs_html
        )

    def open_local_file(self, url: QUrl):
        ok = QDesktopServices.openUrl(url)
        if not ok:
            local_path = url.toLocalFile() or url.toString()
            QMessageBox.warning(
                self,
                "Open File",
                f"Windows could not open:\n\n{local_path}",
            )

    def show_error(self, trace_text: str):
        title, message = friendly_error(trace_text)
        safe_title = html.escape(title)
        safe_message = html.escape(message).replace("\n", "<br>")
        error_html = (
            conversation_separator_html()
            + "<div style='margin:8px 0 12px 0;padding:12px;"
            "border:1px solid #c94b4b;border-radius:6px;background:#fff7f7;'>"
            f"<div style='font-size:14px;font-weight:700;margin-bottom:7px;'>{safe_title}</div>"
            f"<div style='font-size:13px;line-height:1.45;'>{safe_message}</div>"
            "</div>"
        )

        if self.tabs.currentIndex() == 1:
            self.classic_browser.append(error_html)
        else:
            self.rag_browser.append(error_html)

        self.status_label.setText("Request failed")

# ============================================================
# MAIN
# ============================================================


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyleSheet(APP_STYLE)

    if APP_ICON_FILE.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_FILE)))

    font = QFont()
    font.setPointSize(9)
    app.setFont(font)

    window = PlatosDiscipleMainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
