"""Shared config: point Google ADK / google-genai at Vertex AI using api_key.json.

Import this module before anything from google.adk so the env vars are set.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
KEY_PATH = ROOT / "api_key.json"
SURVEYS_DIR = ROOT / "surveys"
RESPONSES_DIR = ROOT / "responses"

PROJECT_ID = json.loads(KEY_PATH.read_text())["project_id"]

# Live models are NOT served from the "global" location - use a region.
LOCATION = os.environ.get("GEMINI_LIVE_LOCATION", "us-central1")
MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-live-2.5-flash-native-audio")
# Female prebuilt voice; the Singaporean accent is steered via the agent's persona prompt.
VOICE = os.environ.get("GEMINI_VOICE", "Aoede")

# Max length of one voice call, as a cost guard (max_llm_calls doesn't apply to live).
MAX_SESSION_SECONDS = int(os.environ.get("MAX_SESSION_SECONDS", "900"))
# How long a pre-connected ("warm") line waits for Start before it is closed and re-opened.
WARM_LINE_SECONDS = int(os.environ.get("WARM_LINE_SECONDS", "300"))

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(KEY_PATH.resolve()))

