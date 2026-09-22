# sec_download_us_corp.py

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone

import requests
import sec_download_us_corp_config as cfg


SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions/"
SEC_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/"

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
    f"sec_download_us_corp_{RUN_START.strftime('%Y%m%d_%H%M%S')}.log",
)

logger = logging.getLogger("sec_download_us_corp")
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
    "Accept": "application/json,text/html,*/*",
})

TOTAL_DOWNLOADED = 0
TOTAL_SKIPPED = 0
TOTAL_FAILED = 0
TOTAL_BYTES = 0
TOTAL_META_WRITTEN = 0


def verbose_info(message, *args):
    if getattr(cfg, "LOG_VERBOSE", False):
        logger.info(message, *args)


def sec_get_json(url):
    time.sleep(cfg.REQUEST_DELAY_SECONDS)
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def sec_get_bytes(url):
    time.sleep(cfg.REQUEST_DELAY_SECONDS)
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def get_ticker_map():
    verbose_info("Loading SEC ticker list | URL=%s", SEC_TICKER_URL)
    data = sec_get_json(SEC_TICKER_URL)

    result = {}
    for row in data.values():
        ticker = row["ticker"].upper().strip()
        result[ticker] = {
            "cik": int(row["cik_str"]),
            "title": row["title"],
        }

    verbose_info("SEC ticker list loaded | Count=%s", len(result))
    return result


def rows_from_column_data(data):
    accession_numbers = data.get("accessionNumber", [])
    forms = data.get("form", [])
    filing_dates = data.get("filingDate", [])
    primary_documents = data.get("primaryDocument", [])

    rows = []
    for i in range(len(accession_numbers)):
        rows.append({
            "accessionNumber": accession_numbers[i] if i < len(accession_numbers) else "",
            "filingDate": filing_dates[i] if i < len(filing_dates) else "",
            "form": forms[i] if i < len(forms) else "",
            "primaryDocument": primary_documents[i] if i < len(primary_documents) else "",
        })
    return rows


def load_all_filings(cik):
    cik10 = str(cik).zfill(10)
    url = f"{SEC_SUBMISSIONS_BASE}CIK{cik10}.json"

    verbose_info("Loading filings | CIK=%s | URL=%s", cik, url)
    data = sec_get_json(url)

    filings = []
    recent = data.get("filings", {}).get("recent", {})
    filings.extend(rows_from_column_data(recent))

    for item in data.get("filings", {}).get("files", []):
        filename = item.get("name")
        if not filename:
            continue

        history_url = f"{SEC_SUBMISSIONS_BASE}{filename}"
        verbose_info("Loading older history | File=%s", filename)
        old_data = sec_get_json(history_url)
        filings.extend(rows_from_column_data(old_data))

    return filings


def subtract_years(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year - years)


def make_download_url(cik, accession_number, primary_document):
    cik_no_zero = str(int(cik))
    accession_no_dash = accession_number.replace("-", "")
    return (
        f"{SEC_ARCHIVE_BASE}"
        f"{cik_no_zero}/"
        f"{accession_no_dash}/"
        f"{primary_document}"
    )


def safe_filename(value):
    for c in '<>:"/\\|?*':
        value = value.replace(c, "_")
    return value


def meta_path_for(document_path):
    return document_path + cfg.META_SUFFIX


def build_meta(
    *, sector, ticker, company_name, cik, filing,
    sec_url, output_name, output_path
):
    # IMPORTANT: insertion order is intentional. These are the first fields
    # downstream Document ingestion should see in the JSON.
    return {
        "fact_type": cfg.FACT_TYPE,
        "sector": sector,
        "ticker": ticker,
        "form_type": filing["form"],

        "country": cfg.COUNTRY,
        "company_name": company_name,
        "cik": cik,
        "filing_date": filing["filingDate"],
        "accession_number": filing["accessionNumber"],
        "primary_document": filing["primaryDocument"],
        "source": "SEC EDGAR",
        "source_url": sec_url,
        "document_filename": output_name,
        "document_path": output_path,
    }


def read_download_status(meta_path):
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f).get("download_status")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def write_meta_json(meta_path, meta):
    global TOTAL_META_WRITTEN
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    TOTAL_META_WRITTEN += 1


def download_company(sector, company_cfg, company_info):
    global TOTAL_DOWNLOADED, TOTAL_SKIPPED, TOTAL_FAILED, TOTAL_BYTES

    ticker = company_cfg["ticker"].upper().strip()
    cik = company_info["cik"]
    company_name = company_info["title"]
    ticker_start = datetime.now()

    verbose_info("=" * 80)
    logger.info(
        "Ticker start | Sector=%s | Ticker=%s | Company=%s | CIK=%s",
        sector, ticker, company_name, cik,
    )

    sector_folder = os.path.join(DOWNLOAD_ROOT, safe_filename(sector))
    ticker_folder = os.path.join(sector_folder, safe_filename(ticker))
    os.makedirs(ticker_folder, exist_ok=True)

    filings = load_all_filings(cik)
    cutoff = subtract_years(date.today(), cfg.YEARS_BACK)

    selected = []
    for filing in filings:
        form = filing.get("form", "").strip()
        filing_date_text = filing.get("filingDate", "").strip()
        primary_document = filing.get("primaryDocument", "").strip()
        accession_number = filing.get("accessionNumber", "").strip()

        if form not in cfg.FORM_TYPES:
            continue
        if not filing_date_text or not primary_document or not accession_number:
            continue

        try:
            filing_date = datetime.strptime(filing_date_text, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(
                "Bad filing date | Ticker=%s | Date=%s | Accession=%s",
                ticker, filing_date_text, accession_number,
            )
            continue

        if filing_date < cutoff:
            continue

        selected.append(filing)

    selected.sort(key=lambda x: x["filingDate"])

    verbose_info(
        "Selected filings | Sector=%s | Ticker=%s | Count=%s | Cutoff=%s",
        sector, ticker, len(selected), cutoff,
    )

    td = ts = tf = tb = 0

    for filing in selected:
        accession_number = filing["accessionNumber"]
        filing_date = filing["filingDate"]
        form = filing["form"]
        primary_document = filing["primaryDocument"]

        extension = os.path.splitext(primary_document)[1] or ".htm"
        output_name = safe_filename(
            f"{ticker}_{filing_date}_{form}_{accession_number}{extension}"
        )
        output_path = os.path.join(ticker_folder, output_name)
        meta_path = meta_path_for(output_path)
        url = make_download_url(cik, accession_number, primary_document)

        meta = build_meta(
            sector=sector,
            ticker=ticker,
            company_name=company_name,
            cik=cik,
            filing=filing,
            sec_url=url,
            output_name=output_name,
            output_path=output_path,
        )
        status = read_download_status(meta_path) if cfg.WRITE_META_JSON else None
        document_ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
        download_complete = document_ok and (
            status == "success" if cfg.WRITE_META_JSON else True
        )

        if cfg.SKIP_EXISTING and download_complete:
            size = os.path.getsize(output_path)
            verbose_info(
                "SKIP | Sector=%s | Ticker=%s | Form=%s | FilingDate=%s | Bytes=%s | File=%s",
                sector, ticker, form, filing_date, size, output_name,
            )
            ts += 1
            TOTAL_SKIPPED += 1
            continue

        # Anything other than a complete document + success meta is restarted.
        for stale_path in (output_path, meta_path):
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
                "GET | Sector=%s | Ticker=%s | Form=%s | FilingDate=%s | URL=%s",
                sector, ticker, form, filing_date, url,
            )

            content = sec_get_bytes(url)
            with open(output_path, "wb") as f:
                f.write(content)

            if cfg.WRITE_META_JSON:
                meta.update({
                    "download_status": "success",
                    "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                })
                write_meta_json(meta_path, meta)

            size = len(content)
            verbose_info(
                "SAVED | Sector=%s | Ticker=%s | Form=%s | FilingDate=%s | Bytes=%s | File=%s",
                sector, ticker, form, filing_date, size, output_name,
            )

            td += 1
            tb += size
            TOTAL_DOWNLOADED += 1
            TOTAL_BYTES += size

        except Exception as exc:
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

            tf += 1
            TOTAL_FAILED += 1
            logger.exception(
                "ERROR | Sector=%s | Ticker=%s | Form=%s | FilingDate=%s | Accession=%s | URL=%s",
                sector, ticker, form, filing_date, accession_number, url,
            )

    elapsed = (datetime.now() - ticker_start).total_seconds()
    logger.info(
        "Ticker finished | Sector=%s | Ticker=%s | Downloaded=%s | Skipped=%s | "
        "Failed=%s | DownloadedBytes=%s | ElapsedSeconds=%.2f",
        sector, ticker, td, ts, tf, tb, elapsed,
    )


def configured_tickers():
    values = []
    for sector, companies in cfg.US_CORP_UNIVERSE.items():
        for company in companies:
            values.append(f"{sector}:{company['ticker']}")
    return values


def main():
    logger.info("=" * 80)
    logger.info("SEC EDGAR US CORP FACT DOWNLOAD RUN START")
    verbose_info("FactType=%s", cfg.FACT_TYPE)
    verbose_info("ScriptDir=%s", SCRIPT_DIR)
    verbose_info("LogRoot=%s", LOG_ROOT)
    verbose_info("DownloadRoot=%s", DOWNLOAD_ROOT)
    verbose_info("Universe=%s", ",".join(configured_tickers()))
    verbose_info("YearsBack=%s", cfg.YEARS_BACK)
    verbose_info("FormTypes=%s", ",".join(cfg.FORM_TYPES))

    try:
        ticker_map = get_ticker_map()

        for sector, companies in cfg.US_CORP_UNIVERSE.items():
            for company_cfg in companies:
                ticker = company_cfg["ticker"].upper().strip()
                if not ticker:
                    continue

                if ticker not in ticker_map:
                    logger.error("Ticker not found | Sector=%s | Ticker=%s", sector, ticker)
                    continue

                try:
                    download_company(sector, company_cfg, ticker_map[ticker])
                except Exception:
                    global TOTAL_FAILED
                    TOTAL_FAILED += 1
                    logger.exception(
                        "Ticker-level failure | Sector=%s | Ticker=%s",
                        sector, ticker,
                    )

    finally:
        run_end = datetime.now()
        elapsed = (run_end - RUN_START).total_seconds()

        logger.info("=" * 80)
        logger.info("SEC EDGAR US CORP FACT DOWNLOAD RUN END")
        logger.info("StartTime=%s", RUN_START.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("EndTime=%s", run_end.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("ElapsedSeconds=%.2f", elapsed)
        logger.info(
            "TOTAL | Downloaded=%s | Skipped=%s | Failed=%s | DownloadedBytes=%s | MetaWritten=%s",
            TOTAL_DOWNLOADED, TOTAL_SKIPPED, TOTAL_FAILED, TOTAL_BYTES, TOTAL_META_WRITTEN,
        )
        verbose_info("LogFile=%s", LOG_FILE)
        logger.info("=" * 80)


if __name__ == "__main__":
    main()
