# sec_config_us_corp.py
# SEC EDGAR downloader configuration for US corporate facts

import os


# This config file normally lives in:
#   <project root>\process\download_sec_edgar\
# Therefore two parent levels above this file is the platos_disciple root.
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

FACT_TYPE = "US corp fact"
COUNTRY = "US"

# Domain -> representative large-cap / industry-leading public companies.
US_CORP_UNIVERSE = {
    "IT": [
        {"ticker": "MSFT"},
        {"ticker": "AAPL"},
    ],
    "Semiconductor": [
        {"ticker": "NVDA"},
        {"ticker": "INTC"},
    ],
    "Communication_Media": [
        {"ticker": "GOOGL"},
        {"ticker": "META"},
    ],
    "Telecom": [
        {"ticker": "VZ"},
        {"ticker": "T"},
    ],
    "Healthcare": [
        {"ticker": "UNH"},
        {"ticker": "ELV"},
    ],
    "Pharmaceutical": [
        {"ticker": "LLY"},
        {"ticker": "JNJ"},
    ],
    "Financial": [
        {"ticker": "JPM"},
        {"ticker": "BAC"},
    ],
    "Insurance": [
        {"ticker": "CB"},
        {"ticker": "PGR"},
    ],
    "Real_Estate": [
        {"ticker": "PLD"},
        {"ticker": "EQIX"},
    ],
    "Manufacturing": [
        {"ticker": "CAT"},
        {"ticker": "DE"},
    ],
    "Defense_Aerospace": [
        {"ticker": "BA"},
        {"ticker": "RTX"},
    ],
    "Energy": [
        {"ticker": "XOM"},
        {"ticker": "CVX"},
    ],
    "Utility": [
        {"ticker": "NEE"},
        {"ticker": "SO"},
    ],
    "Consumer_Staples_Retail": [
        {"ticker": "WMT"},
        {"ticker": "COST"},
    ],
    "Consumer_Discretionary": [
        {"ticker": "AMZN"},
        {"ticker": "HD"},
    ],
    "Logistics": [
        {"ticker": "UPS"},
        {"ticker": "FDX"},
    ],
    "Transportation": [
        {"ticker": "UNP"},
        {"ticker": "DAL"},
    ],
}
YEARS_BACK = 5

FORM_TYPES = [
    "10-K",
    "10-Q",
    "8-K",
]

# Output paths. They may also be changed to any absolute Windows paths, 
# Relative paths, if used, are resolved from PROJECT_ROOT (not the current
# working directory and not the process\download_sec_edgar script folder).
DOWNLOAD_FOLDER = os.path.join("data", "downloaded_sec_edgar")
LOG_FOLDER = os.path.join("logs", "download_sec_edgar")

# False (default): compact log — run summary + one summary line per ticker + errors only.
# True: detailed SEC request / filing / skip / save logging.
LOG_VERBOSE = False

# One sidecar JSON will be generated beside every downloaded SEC filing.
WRITE_META_JSON = True
META_SUFFIX = ".meta.json"

# Replace with a real monitored email address before production use.
USER_AGENT = "PlatosDisciple/1.0 your-email@example.com"

REQUEST_DELAY_SECONDS = 0.25
SKIP_EXISTING = True
