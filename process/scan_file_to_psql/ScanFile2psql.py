# ScanFile2psql.py
# pip install "psycopg[binary]" pypdf python-docx openpyxl xlrd python-pptx pywin32 tqdm beautifulsoup4 charset-normalizer

import os
import sys
import traceback
import logging
import json

# Hide noisy, non-fatal pypdf warnings such as:
# "Early EOD in RunLengthDecode of inline image, using fallback."
# Actual extraction exceptions are NOT hidden; main() still records them
# as Failed in Document/ProcessLog and continues to the next file.
logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pypdf._reader").setLevel(logging.ERROR)
logging.getLogger("pypdf._page").setLevel(logging.ERROR)
logging.getLogger("pypdf.filters").setLevel(logging.ERROR)
from pathlib import Path
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb
from tqdm import tqdm

# =====================================================
# CONFIG
# =====================================================

SCRIPT_DIR = Path(__file__).resolve().parent

# Locate the Plato's Disciple project root without depending on the current
# working directory or on a fixed process subfolder depth.
PROJECT_ROOT = next(
    (
        folder
        for folder in (SCRIPT_DIR, *SCRIPT_DIR.parents)
        if (folder / "config" / "config_general.py").is_file()
    ),
    None,
)

if PROJECT_ROOT is None:
    raise RuntimeError(
        "Cannot find config/config_general.py above ScanDoc.py"
    )

project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

try:
    from config.config_general import (
        CHUNK_OVERLAP,
        CHUNK_SIZE,
        DATA_PATH,
        DB_HOST,
        DB_NAME,
        DB_PASSWORD,
        DB_PORT,
        DB_USER,
    )
except ImportError as exc:
    raise RuntimeError(
        f"Cannot load configuration from: "
        f"{PROJECT_ROOT / 'config' / 'config_general.py'}"
    ) from exc

KB_ROOT_FOLDER = DATA_PATH

POSTGRES = {
    "host": DB_HOST,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "dbname": DB_NAME,
    "port": int(DB_PORT),
}

EXTS = {
    ".pdf", ".xlsx", ".xlsm", ".xls",
    ".docx", ".doc",
    ".pptx", ".ppt",
    ".txt", ".csv",
    ".htm", ".html",
    ".mhtml", ".mht"
}

def word_count(text):
    return len((text or "").split())


def token_estimate(text):
    return max(1, int(len(text or "") / 4))


def load_sidecar_metadata(path):
    """
    Load optional sidecar metadata from:

        <entity filename>.meta.json

    Example:
        MSFT_2011-10-20_10-Q_....htm
        MSFT_2011-10-20_10-Q_....htm.meta.json

    Returns:
        (metadata_dict_or_list_or_scalar, sidecar_path_or_none)

    If no sidecar exists, metadata is None.
    Invalid JSON raises an exception so the document is not silently imported
    with bad/missing metadata.
    """
    sidecar = Path(str(path) + ".meta.json")

    if not sidecar.is_file():
        return None, None

    try:
        with sidecar.open("r", encoding="utf-8-sig") as f:
            metadata = json.load(f)
    except Exception as exc:
        raise ValueError(
            f"Invalid metadata JSON sidecar: {sidecar}"
        ) from exc

    return metadata, sidecar


def chunk_text(text):
    text = text or ""
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    start = 0

    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append((chunk, start, end))
        start += step

    return chunks


def folder_levels(root, path):
    rel = os.path.relpath(path, root)
    parts = Path(rel).parts[:-1]
    lv = list(parts[:4]) + [None] * 4
    return rel, parts, lv[:4]


def extract_pdf(path):
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []

    for i, page in enumerate(reader.pages, start=1):
        pages.append({
            "page_no": i,
            "text": page.extract_text() or ""
        })

    full_text = "\n\n".join(p["text"] for p in pages)
    return full_text, pages, []


def extract_docx(path):
    """
    Extract text from a DOCX in document order.

    python-docx's doc.paragraphs only returns top-level paragraphs. Many saved
    web articles and generated reports put all visible text inside Word table
    cells, so this function reads both paragraphs and tables, including nested
    tables inside cells.
    """
    from docx import Document
    from docx.document import Document as _Document
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    def iter_block_items(parent):
        """Yield Paragraph and Table objects in their original XML order."""
        if isinstance(parent, _Document):
            parent_element = parent.element.body
        elif isinstance(parent, _Cell):
            parent_element = parent._tc
        else:
            raise TypeError(f"Unsupported DOCX parent type: {type(parent)!r}")

        for child in parent_element.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    def collect_blocks(parent, output):
        for block in iter_block_items(parent):
            if isinstance(block, Paragraph):
                value = (block.text or "").strip()
                if value:
                    output.append(value)
            elif isinstance(block, Table):
                for row in block.rows:
                    seen_cells = set()
                    for cell in row.cells:
                        # Merged cells may be returned more than once in one row.
                        cell_key = id(cell._tc)
                        if cell_key in seen_cells:
                            continue
                        seen_cells.add(cell_key)
                        collect_blocks(cell, output)

    doc = Document(path)
    text_parts = []
    collect_blocks(doc, text_parts)

    full_text = "\n\n".join(text_parts).strip()
    return full_text, [], []


def extract_xlsx(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets = []

    for ws in wb.worksheets:
        rows = []
        row_count = 0
        col_count = 0

        for row in ws.iter_rows(values_only=True):
            vals = [str(v) for v in row if v is not None]
            if vals:
                row_count += 1
                col_count = max(col_count, len(vals))
                rows.append(" | ".join(vals))

        text = "\n".join(rows)

        sheets.append({
            "sheet_name": ws.title,
            "used_range": ws.calculate_dimension(),
            "row_count": row_count,
            "column_count": col_count,
            "text": text
        })

    full_text = "\n\n".join(
        f"[Sheet: {s['sheet_name']}]\n{s['text']}"
        for s in sheets
        if s["text"].strip()
    )

    return full_text, [], sheets


def extract_pptx(path):
    from pptx import Presentation

    prs = Presentation(path)
    pages = []

    for i, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text:
                texts.append(shape.text)

        pages.append({
            "page_no": i,
            "text": "\n".join(texts)
        })

    full_text = "\n\n".join(p["text"] for p in pages)
    return full_text, pages, []


def extract_txt(path):
    for enc in ("utf-8", "utf-8-sig", "cp1252", "gb18030"):
        try:
            text = Path(path).read_text(encoding=enc, errors="ignore")
            return text, [], []
        except Exception:
            pass

    return "", [], []


def _normalize_charset_name(charset):
    """Normalize common and malformed charset labels found in saved web pages."""
    if not charset:
        return None

    value = str(charset).strip().strip("\"'").lower()
    aliases = {
        "utf8": "utf-8",
        "utf_8": "utf-8",
        "gb2312": "gb18030",
        "gb_2312-80": "gb18030",
        "x-gbk": "gb18030",
        "gbk": "gb18030",
        "cp936": "gb18030",
        "windows-936": "gb18030",
        "big5-hkscs": "big5hkscs",
        "unicode": "utf-8",
    }
    return aliases.get(value, value)


def _html_declared_charsets(raw_bytes):
    """Read charset declarations directly from the beginning of an HTML byte stream."""
    import re

    head = raw_bytes[:16384].decode("ascii", errors="ignore")
    found = []

    patterns = (
        r"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9._-]+)",
        r"<meta[^>]+content\s*=\s*[\"'][^\"']*charset\s*=\s*([A-Za-z0-9._-]+)",
        r"<\?xml[^>]+encoding\s*=\s*[\"']\s*([A-Za-z0-9._-]+)",
    )

    for pattern in patterns:
        for match in re.findall(pattern, head, flags=re.IGNORECASE):
            charset = _normalize_charset_name(match)
            if charset and charset not in found:
                found.append(charset)

    return found


def _decode_quality(text):
    """
    Lower score is better.

    Penalizes replacement characters, null/control characters and common
    mojibake patterns. This lets us choose between UTF-8, GB18030, Big5, etc.
    """
    if not text:
        return 10**9

    replacement = text.count("\ufffd")
    nulls = text.count("\x00")
    controls = sum(
        1 for ch in text
        if ord(ch) < 32 and ch not in "\r\n\t"
    )

    mojibake_markers = (
        "Ã", "Â", "â€™", "â€œ", "â€", "ðŸ", "锟斤拷", "鏂囧瓧"
    )
    mojibake = sum(text.count(marker) for marker in mojibake_markers)

    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    printable_ratio = printable / max(1, len(text))

    return (
        replacement * 1000
        + nulls * 500
        + controls * 50
        + mojibake * 100
        + int((1.0 - printable_ratio) * 1000)
    )


def _decode_web_bytes(raw_bytes, declared_charset=None, content_type=None):
    """
    Decode one MIME text part without silently destroying Chinese characters.

    Priority:
      1. BOM
      2. MIME charset
      3. HTML meta/XML charset
      4. Common web encodings
      5. charset-normalizer detection
    """
    if not raw_bytes:
        return ""

    candidates = []

    def add(charset):
        charset = _normalize_charset_name(charset)
        if charset and charset not in candidates:
            candidates.append(charset)

    # BOM-aware codecs should be tried first.
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        add("utf-8-sig")
    elif raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        add("utf-16")
    elif raw_bytes.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        add("utf-32")

    add(declared_charset)

    if (content_type or "").lower() == "text/html":
        for charset in _html_declared_charsets(raw_bytes):
            add(charset)

    # Encodings frequently encountered in Chinese/English MHTML archives.
    for charset in (
        "utf-8-sig", "utf-8", "gb18030", "big5hkscs",
        "utf-16", "windows-1252", "latin-1"
    ):
        add(charset)

    decoded = []

    for charset in candidates:
        try:
            value = raw_bytes.decode(charset, errors="strict")
            decoded.append((_decode_quality(value), charset, value))
        except (LookupError, UnicodeDecodeError):
            continue

    # charset-normalizer is especially useful when MIME headers are missing/wrong.
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw_bytes).best()
        if best is not None:
            detected_charset = _normalize_charset_name(best.encoding)
            value = str(best)
            decoded.append((_decode_quality(value), detected_charset, value))
    except Exception:
        pass

    if decoded:
        decoded.sort(key=lambda item: item[0])
        return decoded[0][2]

    # Last resort: preserve evidence of bad bytes instead of silently dropping them.
    return raw_bytes.decode("utf-8", errors="replace")


def _clean_html_to_text(html):
    """Convert HTML to readable, search-friendly text."""
    import re
    from bs4 import BeautifulSoup, Comment

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title:
        title = soup.title.get_text(" ", strip=True)

    for tag in soup([
        "script", "style", "noscript", "template",
        "svg", "canvas", "iframe", "object"
    ]):
        tag.decompose()

    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    # Preserve useful visual boundaries before flattening.
    for tag in soup.find_all(["br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"]):
        tag.insert_after("\n")

    body_text = soup.get_text(separator=" ", strip=True)
    body_text = body_text.replace("\xa0", " ")
    body_text = re.sub(r"[ \t\f\v]+", " ", body_text)
    body_text = re.sub(r" *\n *", "\n", body_text)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

    if title and title not in body_text[:500]:
        return f"[Title: {title}]\n\n{body_text}".strip()

    return body_text


def _validate_extracted_text(text, path):
    """Fail clearly when extraction produced replacement-character garbage."""
    if not text or not text.strip():
        return

    replacement_count = text.count("\ufffd")
    replacement_ratio = replacement_count / max(1, len(text))

    if replacement_count >= 20 and replacement_ratio >= 0.01:
        raise ValueError(
            f"MHTML decoding produced too many replacement characters "
            f"({replacement_count}, {replacement_ratio:.2%}) in: {path}"
        )


def extract_html(path):
    """
    Extract readable text from ordinary .htm / .html files.

    Reuses the same robust charset detection and HTML cleanup logic used
    for MHTML, including UTF-8 / GB18030 / Big5 / Windows encodings.
    """
    raw_bytes = Path(path).read_bytes()

    text = _decode_web_bytes(
        raw_bytes,
        declared_charset=None,
        content_type="text/html"
    )

    full_text = _clean_html_to_text(text)
    _validate_extracted_text(full_text, path)

    return full_text, [], []


def extract_mhtml(path):
    """
    Robustly extract Chinese/English text from .mhtml / .mht MIME archives.

    Handles:
      - multipart MIME structure
      - base64 and quoted-printable transfer encoding
      - MIME charset declarations
      - HTML meta charset declarations
      - UTF-8, GB18030/GBK/GB2312, Big5 and Windows encodings
      - missing or incorrect charset headers

    Embedded images, CSS, JavaScript and binary resources are ignored.
    HTML is preferred; text/plain is used only as a fallback.
    """
    import re
    from email import policy
    from email.parser import BytesParser

    with open(path, "rb") as f:
        message = BytesParser(policy=policy.default).parse(f)

    html_parts = []
    plain_parts = []

    parts = message.walk() if message.is_multipart() else [message]

    for part in parts:
        if part.is_multipart():
            continue

        content_type = (part.get_content_type() or "").lower()
        if content_type not in ("text/html", "text/plain"):
            continue

        # get_payload(decode=True) correctly reverses base64 and
        # quoted-printable transfer encoding before character decoding.
        raw_payload = part.get_payload(decode=True)

        if raw_payload is None:
            payload = part.get_payload()
            if isinstance(payload, str):
                raw_payload = payload.encode("latin-1", errors="replace")
            else:
                continue

        declared_charset = part.get_content_charset()
        content = _decode_web_bytes(
            raw_payload,
            declared_charset=declared_charset,
            content_type=content_type
        )

        if not content or not content.strip():
            continue

        if content_type == "text/html":
            html_parts.append(content)
        else:
            plain_parts.append(content)

    extracted_parts = []

    for html in html_parts:
        text = _clean_html_to_text(html)
        if text:
            extracted_parts.append(text)

    if not extracted_parts:
        for plain in plain_parts:
            plain = plain.replace("\xa0", " ")
            plain = re.sub(r"[ \t\f\v]+", " ", plain)
            plain = re.sub(r" *\n *", "\n", plain)
            plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
            if plain:
                extracted_parts.append(plain)

    # Remove exact duplicate MIME text parts while preserving order.
    unique_parts = []
    seen = set()

    for value in extracted_parts:
        key = value.strip()
        if key and key not in seen:
            seen.add(key)
            unique_parts.append(key)

    full_text = "\n\n".join(unique_parts).strip()
    _validate_extracted_text(full_text, path)

    return full_text, [], []


def extract_old_office(path):
    """
    Process .doc / .xls / .ppt
    Need Windows Microsoft Office.
    """
    import win32com.client as win32

    ext = Path(path).suffix.lower()

    if ext == ".doc":
        # Use a separate Word COM instance for better isolation.
        # Some legacy .doc files do not expose Content reliably through
        # dynamic COM dispatch, so Range().Text is safer than Content.Text.
        word = None
        doc = None

        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0

            doc = word.Documents.Open(
                FileName=path,
                ReadOnly=True,
                ConfirmConversions=False,
                AddToRecentFiles=False,
                Visible=False
            )

            text = doc.Range().Text or ""
            return text, [], []

        finally:
            if doc is not None:
                try:
                    doc.Close(False)
                except Exception:
                    pass

            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass

    if ext == ".xls":
        # IMPORTANT:
        # Do NOT use Excel COM for legacy .xls files here.
        # A broken/unreachable/network .xls can make Excel show a modal dialog
        # ("cannot be accessed") and block the whole unattended scan forever.
        #
        # xlrd reads the .xls file directly in-process. If the file is bad,
        # locked, unreachable or corrupt, it raises an exception immediately.
        # The outer main() exception handler then records the failure in
        # ProcessLog / Document and continues with the next file.
        import xlrd

        # Quick OS-level read test. This also fails fast for many unavailable
        # network files before xlrd starts parsing.
        with open(path, "rb") as f:
            f.read(1)

        book = xlrd.open_workbook(path, on_demand=True)
        sheets = []

        try:
            for ws in book.sheets():
                rows = []
                row_count = 0
                col_count = 0

                for r in range(ws.nrows):
                    cells = []

                    for c in range(ws.ncols):
                        value = ws.cell_value(r, c)

                        if value is None or value == "":
                            continue

                        # Keep dates reasonably readable when xlrd identifies them.
                        cell = ws.cell(r, c)
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            try:
                                value = xlrd.xldate.xldate_as_datetime(
                                    value, book.datemode
                                ).isoformat(sep=" ")
                            except Exception:
                                pass

                        cells.append(str(value))

                    if cells:
                        row_count += 1
                        col_count = max(col_count, len(cells))
                        rows.append(" | ".join(cells))

                sheet_text = "\n".join(rows)

                used_range = (
                    f"A1:{xlrd.formula.cellname(max(0, ws.nrows - 1), max(0, ws.ncols - 1))}"
                    if ws.nrows and ws.ncols
                    else ""
                )

                sheets.append({
                    "sheet_name": ws.name,
                    "used_range": used_range,
                    "row_count": row_count,
                    "column_count": col_count,
                    "text": sheet_text
                })
        finally:
            book.release_resources()

        full_text = "\n\n".join(
            f"[Sheet: {s['sheet_name']}]\n{s['text']}"
            for s in sheets
            if s["text"].strip()
        )

        return full_text, [], sheets

    if ext == ".ppt":
        ppt = win32.Dispatch("PowerPoint.Application")
        pres = ppt.Presentations.Open(path, WithWindow=False)

        pages = []

        for i, slide in enumerate(pres.Slides, start=1):
            texts = []

            for shape in slide.Shapes:
                if shape.HasTextFrame and shape.TextFrame.HasText:
                    texts.append(shape.TextFrame.TextRange.Text)

            pages.append({
                "page_no": i,
                "text": "\n".join(texts)
            })

        pres.Close()
        ppt.Quit()

        full_text = "\n\n".join(p["text"] for p in pages)
        return full_text, pages, []

    return "", [], []


def extract_file(path):
    ext = Path(path).suffix.lower()

    if ext == ".pdf":
        return extract_pdf(path)

    if ext in (".xlsx", ".xlsm"):
        return extract_xlsx(path)

    if ext == ".docx":
        return extract_docx(path)

    if ext == ".pptx":
        return extract_pptx(path)

    if ext in (".doc", ".xls", ".ppt"):
        return extract_old_office(path)

    if ext in (".txt", ".csv"):
        return extract_txt(path)

    if ext in (".htm", ".html"):
        return extract_html(path)

    if ext in (".mhtml", ".mht"):
        return extract_mhtml(path)

    return "", [], []


def log_process(cur, doc_id, process_name, status, message=None):
    cur.execute("""
        INSERT INTO ProcessLog
        (DocID, ProcessName, Status, StartTime, EndTime, Message)
        VALUES (%s, %s, %s, NOW(), NOW(), %s)
    """, (doc_id, process_name, status, message))


def main():
    conn = psycopg.connect(**POSTGRES)
    cur = conn.cursor()

    root = Path(KB_ROOT_FOLDER)
    files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTS
    ]

    new_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    for p in tqdm(files, desc="Scanning local data files"):
        doc_id = None

        try:
            full_path = str(p)
            rel_path, folder_parts, levels = folder_levels(KB_ROOT_FOLDER, full_path)
            folder_path = str(p.parent)
            stat = p.stat()

            # Identify an existing file only by its full path.
            # If the stored modified timestamp matches the current file timestamp,
            # the file is treated as unchanged and skipped immediately.
            cur.execute("""
                SELECT
                    DocID,
                    ModifiedTime,
                    Metadata
                FROM Document
                WHERE FullPath = %s
                LIMIT 1
            """, (full_path,))
            existing = cur.fetchone()

            current_modified = datetime.fromtimestamp(stat.st_mtime)

            # Optional sidecar JSON belongs to the entity file and is stored in
            # Document.Metadata (jsonb). The sidecar itself is NOT a Document.
            sidecar_metadata, sidecar_path = load_sidecar_metadata(p)

            if existing:
                existing_doc_id = existing[0]
                existing_modified = existing[1]
                existing_metadata = existing[2]

                # MySQL DATETIME commonly stores whole seconds, while Windows may
                # expose fractional seconds. A difference below one second is
                # therefore considered the same timestamp.
                same_modified = (
                    existing_modified is not None
                    and abs((existing_modified - current_modified).total_seconds()) < 1
                )

                if same_modified:
                    # Even if the entity file itself did not change, its
                    # XXXX.meta.json may have been added/changed since the last scan.
                    if existing_metadata != sidecar_metadata:
                        cur.execute("""
                            UPDATE Document
                            SET Metadata = %s
                            WHERE DocID = %s
                        """, (
                            Jsonb(sidecar_metadata) if sidecar_metadata is not None else None,
                            existing_doc_id
                        ))
                        conn.commit()

                    skipped_count += 1
                    continue
            else:
                existing_doc_id = None

            if existing:
                doc_id = existing_doc_id

                # Existing path with a different modified timestamp: delete the old
                # extracted content, then append a fresh extraction for the same DocID.
                cur.execute("DELETE FROM DocumentChunk WHERE DocID = %s", (doc_id,))
                cur.execute("DELETE FROM DocumentPageText WHERE DocID = %s", (doc_id,))
                cur.execute("DELETE FROM DocumentSheetText WHERE DocID = %s", (doc_id,))
                cur.execute("DELETE FROM DocumentText WHERE DocID = %s", (doc_id,))

                cur.execute("""
                    UPDATE Document
                    SET RelativePath = %s,
                        FolderPath = %s,
                        FolderLevel1 = %s,
                        FolderLevel2 = %s,
                        FolderLevel3 = %s,
                        FolderLevel4 = %s,
                        FileName = %s,
                        Extension = %s,
                        FileSizeBytes = %s,
                        ModifiedTime = %s,
                        Metadata = %s,
                        ParseStatus = 'Processing',
                        ParseTime = NOW(),
                        ErrorMessage = NULL
                    WHERE DocID = %s
                """, (
                    rel_path, folder_path,
                    levels[0], levels[1], levels[2], levels[3],
                    p.name, p.suffix.lower(),
                    stat.st_size, current_modified,
                    Jsonb(sidecar_metadata) if sidecar_metadata is not None else None,
                    doc_id
                ))
                updated_count += 1

            else:
                cur.execute("""
                    INSERT INTO Document
                    (
                        FullPath, RelativePath, FolderPath,
                        FolderLevel1, FolderLevel2, FolderLevel3, FolderLevel4,
                        FileName, Extension,
                        FileSizeBytes, ModifiedTime,
                        Metadata,
                        ParseStatus, ParseTime, ErrorMessage
                    )
                    VALUES
                    (
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s,
                        'Processing', NOW(), NULL
                    )
                    RETURNING DocID
                """, (
                    full_path, rel_path, folder_path,
                    levels[0], levels[1], levels[2], levels[3],
                    p.name, p.suffix.lower(),
                    stat.st_size, current_modified,
                    Jsonb(sidecar_metadata) if sidecar_metadata is not None else None
                ))
                doc_id = cur.fetchone()[0]
                new_count += 1

            # Commit the parent Document row and any cleanup before extraction.
            conn.commit()

            log_process(cur, doc_id, "ExtractText", "Started", "Begin extracting text")
            conn.commit()

            full_text, pages, sheets = extract_file(full_path)

            # Do not report Success when an extractor returned no usable text.
            # This catches unsupported DOCX layouts, image-only documents and
            # other silent extraction failures before empty rows are committed.
            if not full_text or not full_text.strip():
                raise ValueError(
                    f"No extractable text was found in file: {full_path}"
                )

            cur.execute("""
                INSERT INTO DocumentText
                (DocID, RawText, CharacterCount, WordCount, ExtractedAt)
                VALUES (%s, %s, %s, %s, NOW())
            """, (
                doc_id,
                full_text,
                len(full_text or ""),
                word_count(full_text)
            ))

            for page in pages:
                cur.execute("""
                    INSERT INTO DocumentPageText
                    (DocID, PageNo, PageText, CharacterCount)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (DocID, PageNo)
                    DO UPDATE SET
                        PageText = EXCLUDED.PageText,
                        CharacterCount = EXCLUDED.CharacterCount
                """, (
                    doc_id,
                    page["page_no"],
                    page["text"],
                    len(page["text"] or "")
                ))

            for sheet in sheets:
                cur.execute("""
                    INSERT INTO DocumentSheetText
                    (
                        DocID, SheetName, UsedRange,
                        RowCount, ColumnCount,
                        SheetText, CharacterCount
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (DocID, SheetName)
                    DO UPDATE SET
                        UsedRange = EXCLUDED.UsedRange,
                        RowCount = EXCLUDED.RowCount,
                        ColumnCount = EXCLUDED.ColumnCount,
                        SheetText = EXCLUDED.SheetText,
                        CharacterCount = EXCLUDED.CharacterCount
                """, (
                    doc_id,
                    sheet["sheet_name"][:255],
                    sheet["used_range"],
                    sheet["row_count"],
                    sheet["column_count"],
                    sheet["text"],
                    len(sheet["text"] or "")
                ))

            chunk_no = 0

            if sheets:
                for sheet in sheets:
                    for chunk, start, end in chunk_text(sheet["text"]):
                        chunk_no += 1
                        cur.execute("""
                            INSERT INTO DocumentChunk
                            (
                                DocID, ChunkNo, ChunkSource,
                                PageNo, SheetName,
                                ChunkText, StartPos, EndPos,
                                CharacterCount, TokenEstimate
                            )
                            VALUES (%s,%s,'Sheet',NULL,%s,%s,%s,%s,%s,%s)
                        """, (
                            doc_id, chunk_no,
                            sheet["sheet_name"][:255],
                            chunk, start, end,
                            len(chunk), token_estimate(chunk)
                        ))

            elif pages:
                for page in pages:
                    for chunk, start, end in chunk_text(page["text"]):
                        chunk_no += 1
                        cur.execute("""
                            INSERT INTO DocumentChunk
                            (
                                DocID, ChunkNo, ChunkSource,
                                PageNo, SheetName,
                                ChunkText, StartPos, EndPos,
                                CharacterCount, TokenEstimate
                            )
                            VALUES (%s,%s,'Page',%s,NULL,%s,%s,%s,%s,%s)
                        """, (
                            doc_id, chunk_no,
                            page["page_no"],
                            chunk, start, end,
                            len(chunk), token_estimate(chunk)
                        ))

            else:
                for chunk, start, end in chunk_text(full_text):
                    chunk_no += 1
                    cur.execute("""
                        INSERT INTO DocumentChunk
                        (
                            DocID, ChunkNo, ChunkSource,
                            PageNo, SheetName,
                            ChunkText, StartPos, EndPos,
                            CharacterCount, TokenEstimate
                        )
                        VALUES (%s,%s,'FullText',NULL,NULL,%s,%s,%s,%s,%s)
                    """, (
                        doc_id, chunk_no,
                        chunk, start, end,
                        len(chunk), token_estimate(chunk)
                    ))

            if chunk_no == 0:
                raise ValueError(
                    f"Text was extracted but no chunks were created: {full_path}"
                )

            cur.execute("""
                UPDATE Document
                SET ParseStatus = 'Success',
                    ParseTime = NOW(),
                    ErrorMessage = NULL
                WHERE DocID = %s
            """, (doc_id,))

            log_process(
                cur,
                doc_id,
                "ExtractText",
                "Success",
                f"Extracted {len(full_text or '')} chars, {chunk_no} chunks"
            )

            conn.commit()

        except Exception:
            failed_count += 1
            err = traceback.format_exc()[-6000:]

            # Roll back any partial text/page/sheet/chunk writes for this file.
            # The Document row has already been committed, so the failure status
            # and ProcessLog row can be written safely after the rollback.
            try:
                conn.rollback()

                if doc_id:
                    cur.execute("""
                        UPDATE Document
                        SET ParseStatus = 'Failed',
                            ParseTime = NOW(),
                            ErrorMessage = %s
                        WHERE DocID = %s
                    """, (err, doc_id))

                    log_process(cur, doc_id, "ExtractText", "Failed", err)

                conn.commit()

            except Exception:
                conn.rollback()
                print("Failed to write error status for:", str(p))
                print(err)

            # Continue with the next file instead of stopping the whole batch.
            continue

    print()
    print("Import summary")
    print("--------------")
    print(f"New files appended:       {new_count}")
    print(f"Modified files replaced: {updated_count}")
    print(f"Unchanged files skipped: {skipped_count}")
    print(f"Failed files:             {failed_count}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
