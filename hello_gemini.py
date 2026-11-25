"""Hello world for Gemini on Vertex AI, authenticated with a service account key.

    python hello_gemini.py          # text model (generate_content)
    python hello_gemini.py --live   # Live API (the model the voice bot uses)
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from google import genai
from google.oauth2 import service_account

KEY_PATH = Path(__file__).parent / "api_key.json"
LOCATION = os.environ.get("GEMINI_LOCATION", "global")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# Live models need a regional endpoint (not "global").
LIVE_LOCATION = os.environ.get("GEMINI_LIVE_LOCATION", "us-central1")
LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-live-2.5-flash-native-audio")


def make_client(location: str) -> genai.Client:
    project_id = json.loads(KEY_PATH.read_text())["project_id"]
    credentials = service_account.Credentials.from_service_account_file(
        KEY_PATH, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        credentials=credentials,
    )


def main() -> None:
    client = make_client(LOCATION)
    response = client.models.generate_content(
        model=MODEL,
        contents="Say hello world in one short, friendly sentence.",
    )
    print(response.text)


async def main_live() -> None:
    client = make_client(LIVE_LOCATION)
    config = {"response_modalities": ["AUDIO"], "output_audio_transcription": {}}
    async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": "Say hello world in one short, friendly sentence."}]},
            turn_complete=True,
        )
        audio_bytes = 0
        transcript = []
        async for msg in session.receive():
            sc = msg.server_content
            if not sc:
                continue
            if sc.model_turn:
                for part in sc.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        audio_bytes += len(part.inline_data.data)
            if sc.output_transcription and sc.output_transcription.text:
                transcript.append(sc.output_transcription.text)
            if sc.turn_complete:
                break
    print(f"[{LIVE_MODEL} @ {LIVE_LOCATION}] transcript: {''.join(transcript).strip()}")
    print(f"received {audio_bytes} bytes of 24 kHz PCM audio")


if __name__ == "__main__":
    if "--live" in sys.argv:
        asyncio.run(main_live())
    else:
        main()
