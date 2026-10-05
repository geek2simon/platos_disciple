# -*- coding: utf-8 -*-
# =====================================================
# PostgreSQL
# =====================================================
DB_HOST = "localhost"
DB_USER = "kb_agent"
DB_PORT = 5432
DB_PASSWORD = "YOUR_OWN_PASSWORD"
DB_NAME = "pd_market_kb"

# =====================================================
# Azure OpenAI API
# something like: https://xxxxx.services.ai.azure.com/openai/v1
# =====================================================

ENDPOINT_URL = "https://YOUR_OWN_URL"
DEPLOYMENT_NAME = "gpt-5.4-mini"
API_KEY = "YOUR_OWN_API_KEY_FROM_AZURE"

# =====================================================
# downloaded folder/path
# =====================================================
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data"

# =====================================================
# Document scanning/chunking
# =====================================================
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150