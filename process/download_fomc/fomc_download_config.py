# fomc_download_config.py
# Federal Reserve FOMC document downloader configuration

import os


# This config file normally lives in:
#   <project root>\process\download_fomc\
# Therefore two parent levels above this file is the platos_disciple root.
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

# Keep these four fields aligned with the SEC sidecar metadata used by the
# document-ingestion pipeline.
FACT_TYPE = "US macro fact"
SECTOR = "Macro_Economy"
TICKER = ""
COUNTRY = "US"

# The current Federal Reserve calendar contains recent meetings and links to
# statements, minutes, projection materials, and press-conference pages.
CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# Recommended FOMC corpus for market research.
# Valid values:
#   statement                   policy decision; PDF also includes implementation details
#   minutes                     detailed discussion, normally released later
#   projections                 Summary of Economic Projections (SEP), when available
#   press_conference_transcript Chair press conference and Q&A
DOCUMENT_TYPES = [
    "statement",
    "minutes",
    "projections",
    "press_conference_transcript",
]

YEARS_BACK = 5

# Relative paths are resolved from PROJECT_ROOT, not from the current working
# directory and not from the process\download_fomc script folder.
DOWNLOAD_FOLDER = os.path.join("data", "downloaded_fomc")
LOG_FOLDER = os.path.join("logs", "download_fomc")

# False: compact log with run totals and one summary line per document type.
# True: include discovery, skip, request, and save details.
LOG_VERBOSE = False

WRITE_META_JSON = True
META_SUFFIX = ".meta.json"
SKIP_EXISTING = True

# A descriptive User-Agent is courteous and makes the caller identifiable.
USER_AGENT = "PlatosDisciple/1.0 your-email@example.com"

REQUEST_DELAY_SECONDS = 0.25
REQUEST_TIMEOUT_SECONDS = 60
REQUEST_RETRIES = 3
REQUEST_BACKOFF_FACTOR = 1.0

# Reject an error page saved with a .pdf extension.
MIN_PDF_BYTES = 500

