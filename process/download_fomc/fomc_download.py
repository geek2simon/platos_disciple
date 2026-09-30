# fomc_download.py

import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import fomc_download_config as cfg


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_config_path(configured_path):
    """Resolve an absolute path, or a path relative to the project root."""
    path = os.path.expanduser(os.path.expandvars(configured_path))
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.abspath(os.path.join(cfg.PROJECT_ROOT, path))


LOG_ROOT = resolve_config_path(cfg.LOG_FOLDER)
DOWNLOAD_ROOT = resolve_config_path(cfg.DOWNLOAD_FOLDER)

os.makedirs(LOG_ROOT, exist_ok=True)
os.makedirs(DOWNLOAD_ROOT, exist_ok=True)

RUN_START = datetime.now()
LOG_FILE = os.path.join(
    LOG_ROOT,
    f"fomc_download_{RUN_START.strftime('%Y%m%d_%H%M%S')}.log",
)

logger = logging.getLogger("fomc_download")
logger.setLevel(logging.INFO)
logger.handlers.clear()

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setFormatter(formatter)
logger.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(formatter)
logger.addHandler(ch)

session = requests.Session()
session.headers.update({
    "User-Agent": cfg.USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/pdf,text/html,*/*",
})

retry = Retry(
    total=cfg.REQUEST_RETRIES,
    connect=cfg.REQUEST_RETRIES,
    read=cfg.REQUEST_RETRIES,
    status=cfg.REQUEST_RETRIES,
    backoff_factor=cfg.REQUEST_BACKOFF_FACTOR,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET",)),
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)
session.mount("http://", adapter)

TOTAL_DISCOVERED = 0
TOTAL_DOWNLOADED = 0
TOTAL_SKIPPED = 0
TOTAL_FAILED = 0
TOTAL_BYTES = 0
TOTAL_META_WRITTEN = 0


DOCUMENT_DEFINITIONS = {
    "statement": {
        "form_type": "FOMC Statement",
        "filename_label": "statement",
    },
    "minutes": {
        "form_type": "FOMC Minutes",
        "filename_label": "minutes",
    },
    "projections": {
        "form_type": "FOMC Summary of Economic Projections",
        "filename_label": "projections",
    },
    "press_conference_transcript": {
        "form_type": "FOMC Press Conference Transcript",
        "filename_label": "press_conference_transcript",
    },
}


class LinkParser(HTMLParser):
    """Collect href values without adding a BeautifulSoup dependency."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        values = dict(attrs)
        href = values.get("href")
        if href:
            self.hrefs.append(href.strip())


def verbose_info(message, *args):
    if getattr(cfg, "LOG_VERBOSE", False):
        logger.info(message, *args)


def fed_get(url):
    time.sleep(cfg.REQUEST_DELAY_SECONDS)
    response = session.get(url, timeout=cfg.REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


def fed_get_text(url):
    response = fed_get(url)
    response.encoding = response.encoding or "utf-8"
    return response.text


def fed_get_bytes(url):
    return fed_get(url).content


def parse_links(html, base_url):
    parser = LinkParser()
    parser.feed(html)
    return [urljoin(base_url, href) for href in parser.hrefs]


def subtract_years(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year - years)


def date_from_yyyymmdd(value):
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def normalize_url(url):
    """Discard fragments so the same Federal Reserve file deduplicates."""
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def add_document(result, document_type, meeting_date, url, discovery_url):
    if document_type not in cfg.DOCUMENT_TYPES:
        return
    if document_type not in DOCUMENT_DEFINITIONS:
        return
    if not meeting_date:
        return

    url = normalize_url(url)
    key = (document_type, meeting_date.isoformat(), url.lower())
    result[key] = {
        "document_type": document_type,
        "meeting_date": meeting_date,
        "source_url": url,
        "discovery_url": discovery_url,
    }


def discover_press_transcript(press_page_url, expected_date):
    """Resolve the transcript PDF linked from an FOMC press-conference page."""
    verbose_info("Loading press conference page | URL=%s", press_page_url)
    html = fed_get_text(press_page_url)
    links = parse_links(html, press_page_url)

    expected = expected_date.strftime("%Y%m%d")
    pattern = re.compile(r"/mediacenter/files/fomcpresconf(\d{8})\.pdf$", re.I)
    for link in links:
        match = pattern.search(urlparse(link).path)
        if match and match.group(1) == expected:
            return link

    # Keep a conservative fallback for minor page-layout changes, but require
    # both a PDF and the meeting date in its filename.
    for link in links:
        path = urlparse(link).path.lower()
        if path.endswith(".pdf") and expected in path and "presconf" in path:
            return link
    return None


def discover_documents():
    """Discover supported documents from the official current FOMC calendar."""
    verbose_info("Loading FOMC calendar | URL=%s", cfg.CALENDAR_URL)
    html = fed_get_text(cfg.CALENDAR_URL)
    links = parse_links(html, cfg.CALENDAR_URL)
    documents = {}

    direct_patterns = (
        (
            "statement",
            re.compile(r"/monetarypolicy/files/monetary(\d{8})a1\.pdf$", re.I),
        ),
        (
            "minutes",
            re.compile(r"/monetarypolicy/files/fomcminutes(\d{8})\.pdf$", re.I),
        ),
        (
            "projections",
            re.compile(r"/monetarypolicy/files/fomcprojtabl(\d{8})\.pdf$", re.I),
        ),
    )
    press_page_pattern = re.compile(
        r"/monetarypolicy/fomcpressconf(\d{8})\.htm$", re.I
    )

    press_pages = []
    for link in links:
        path = urlparse(link).path

        for document_type, pattern in direct_patterns:
            match = pattern.search(path)
            if match:
                add_document(
                    documents,
                    document_type,
                    date_from_yyyymmdd(match.group(1)),
                    link,
                    cfg.CALENDAR_URL,
                )
                break

        press_match = press_page_pattern.search(path)
        if press_match and "press_conference_transcript" in cfg.DOCUMENT_TYPES:
            meeting_date = date_from_yyyymmdd(press_match.group(1))
            if meeting_date:
                press_pages.append((meeting_date, normalize_url(link)))

    cutoff = subtract_years(date.today(), cfg.YEARS_BACK)
    press_pages = sorted(set(press_pages))
    for meeting_date, press_page_url in press_pages:
        if meeting_date < cutoff or meeting_date > date.today():
            continue
        try:
            transcript_url = discover_press_transcript(press_page_url, meeting_date)
            if transcript_url:
                add_document(
                    documents,
                    "press_conference_transcript",
                    meeting_date,
                    transcript_url,
                    press_page_url,
                )
            else:
                logger.warning(
                    "Transcript PDF not found | MeetingDate=%s | Page=%s",
                    meeting_date.isoformat(), press_page_url,
                )
        except Exception:
            logger.exception(
                "Press page discovery failure | MeetingDate=%s | Page=%s",
                meeting_date.isoformat(), press_page_url,
            )

    selected = []
    for document in documents.values():
        meeting_date = document["meeting_date"]
        if cutoff <= meeting_date <= date.today():
            selected.append(document)

    selected.sort(key=lambda x: (x["meeting_date"], x["document_type"]))
    return selected


def safe_filename(value):
    for character in '<>:"/\\|?*':
        value = value.replace(character, "_")
    return value


def meta_path_for(document_path):
    return document_path + cfg.META_SUFFIX


def build_meta(document, output_name, output_path):
    definition = DOCUMENT_DEFINITIONS[document["document_type"]]
    meeting_date = document["meeting_date"].isoformat()

    # Insertion order is intentional. These are the first fields downstream
    # Document ingestion should see in the JSON.
    return {
        "fact_type": cfg.FACT_TYPE,
        "sector": cfg.SECTOR,
        "ticker": cfg.TICKER,
        "form_type": definition["form_type"],

        "country": cfg.COUNTRY,
        "meeting_date": meeting_date,
        "document_date": meeting_date,
        "document_type": document["document_type"],
        "source": "Federal Reserve FOMC",
        "source_url": document["source_url"],
        "discovery_url": document["discovery_url"],
        "document_filename": output_name,
        "document_path": output_path,
    }


def read_download_status(meta_path):
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as file:
            return json.load(file).get("download_status")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def write_meta_json(meta_path, meta):
    global TOTAL_META_WRITTEN
    with open(meta_path, "w", encoding="utf-8") as file:
        json.dump(meta, file, ensure_ascii=False, indent=2)
    TOTAL_META_WRITTEN += 1


def validate_pdf(content, source_url):
    if len(content) < cfg.MIN_PDF_BYTES:
        raise ValueError(
            f"Downloaded file is too small to be a valid PDF: {len(content)} bytes"
        )
    if not content.lstrip().startswith(b"%PDF-"):
        raise ValueError(f"Downloaded content is not a PDF: {source_url}")


def download_document(document):
    global TOTAL_DOWNLOADED, TOTAL_SKIPPED, TOTAL_FAILED, TOTAL_BYTES

    definition = DOCUMENT_DEFINITIONS[document["document_type"]]
    meeting_date = document["meeting_date"]
    year_folder = os.path.join(DOWNLOAD_ROOT, str(meeting_date.year))
    os.makedirs(year_folder, exist_ok=True)

    output_name = safe_filename(
        f"FOMC_{meeting_date.isoformat()}_{definition['filename_label']}.pdf"
    )
    output_path = os.path.join(year_folder, output_name)
    partial_path = output_path + ".part"
    meta_path = meta_path_for(output_path)
    meta = build_meta(document, output_name, output_path)

    status = read_download_status(meta_path) if cfg.WRITE_META_JSON else None
    document_ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
    download_complete = document_ok and (
        status == "success" if cfg.WRITE_META_JSON else True
    )

    if cfg.SKIP_EXISTING and download_complete:
        verbose_info(
            "SKIP | Type=%s | MeetingDate=%s | Bytes=%s | File=%s",
            document["document_type"], meeting_date.isoformat(),
            os.path.getsize(output_path), output_name,
        )
        TOTAL_SKIPPED += 1
        return "skipped", 0

    # Anything other than a complete PDF + success meta is restarted.
    for stale_path in (output_path, partial_path, meta_path):
        if os.path.exists(stale_path):
            os.remove(stale_path)

    meta.update({
        "download_status": "started",
        "download_started_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    if cfg.WRITE_META_JSON:
        write_meta_json(meta_path, meta)

    try:
        verbose_info(
            "GET | Type=%s | MeetingDate=%s | URL=%s",
            document["document_type"], meeting_date.isoformat(),
            document["source_url"],
        )
        content = fed_get_bytes(document["source_url"])
        validate_pdf(content, document["source_url"])

        with open(partial_path, "wb") as file:
            file.write(content)
        os.replace(partial_path, output_path)

        if cfg.WRITE_META_JSON:
            meta.update({
                "download_status": "success",
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                "downloaded_bytes": len(content),
            })
            write_meta_json(meta_path, meta)

        verbose_info(
            "SAVED | Type=%s | MeetingDate=%s | Bytes=%s | File=%s",
            document["document_type"], meeting_date.isoformat(),
            len(content), output_name,
        )
        TOTAL_DOWNLOADED += 1
        TOTAL_BYTES += len(content)
        return "downloaded", len(content)

    except Exception as exc:
        if os.path.exists(partial_path):
            os.remove(partial_path)

        if cfg.WRITE_META_JSON:
            meta.update({
                "download_status": "failed",
                "download_failed_at_utc": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            try:
                write_meta_json(meta_path, meta)
            except Exception:
                logger.exception("META WRITE ERROR | File=%s", meta_path)

        TOTAL_FAILED += 1
        logger.exception(
            "ERROR | Type=%s | MeetingDate=%s | URL=%s",
            document["document_type"], meeting_date.isoformat(),
            document["source_url"],
        )
        return "failed", 0


def validate_config():
    unknown = sorted(set(cfg.DOCUMENT_TYPES) - set(DOCUMENT_DEFINITIONS))
    if unknown:
        raise ValueError("Unknown DOCUMENT_TYPES: " + ", ".join(unknown))
    if cfg.YEARS_BACK < 0:
        raise ValueError("YEARS_BACK must be zero or greater")


def main():
    global TOTAL_DISCOVERED, TOTAL_FAILED

    logger.info("=" * 80)
    logger.info("FEDERAL RESERVE FOMC DOCUMENT DOWNLOAD RUN START")
    verbose_info("FactType=%s", cfg.FACT_TYPE)
    verbose_info("ScriptDir=%s", SCRIPT_DIR)
    verbose_info("LogRoot=%s", LOG_ROOT)
    verbose_info("DownloadRoot=%s", DOWNLOAD_ROOT)
    verbose_info("YearsBack=%s", cfg.YEARS_BACK)
    verbose_info("DocumentTypes=%s", ",".join(cfg.DOCUMENT_TYPES))

    type_totals = {
        document_type: {"discovered": 0, "downloaded": 0, "skipped": 0, "failed": 0}
        for document_type in cfg.DOCUMENT_TYPES
    }

    try:
        validate_config()
        documents = discover_documents()
        TOTAL_DISCOVERED = len(documents)

        for document in documents:
            document_type = document["document_type"]
            type_totals[document_type]["discovered"] += 1
            status, _ = download_document(document)
            type_totals[document_type][status] += 1

        for document_type in cfg.DOCUMENT_TYPES:
            totals = type_totals[document_type]
            logger.info(
                "Document type finished | Type=%s | Discovered=%s | Downloaded=%s | "
                "Skipped=%s | Failed=%s",
                document_type, totals["discovered"], totals["downloaded"],
                totals["skipped"], totals["failed"],
            )

    except Exception:
        TOTAL_FAILED += 1
        logger.exception("FOMC download run failure")

    finally:
        run_end = datetime.now()
        elapsed = (run_end - RUN_START).total_seconds()

        logger.info("=" * 80)
        logger.info("FEDERAL RESERVE FOMC DOCUMENT DOWNLOAD RUN END")
        logger.info("StartTime=%s", RUN_START.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("EndTime=%s", run_end.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("ElapsedSeconds=%.2f", elapsed)
        logger.info(
            "TOTAL | Discovered=%s | Downloaded=%s | Skipped=%s | Failed=%s | "
            "DownloadedBytes=%s | MetaWritten=%s",
            TOTAL_DISCOVERED, TOTAL_DOWNLOADED, TOTAL_SKIPPED, TOTAL_FAILED,
            TOTAL_BYTES, TOTAL_META_WRITTEN,
        )
        verbose_info("LogFile=%s", LOG_FILE)
        logger.info("=" * 80)


if __name__ == "__main__":
    main()

