# AIA Customer Call-back Verification (Voice Bot)

A voice agent ("Mei Ling", AIA Singapore Customer Verification Unit) that runs post-sale
call-back surveys aligned to MAS Fair Dealing / Balanced Scorecard, and flags possible
mis-selling. Built with Google ADK + Gemini Live (native audio) on Vertex AI.

## Prerequisites

- Python 3.12+ (tested on 3.14)
- `api_key.json` in the project root: a Google Cloud service-account key with the
  **Vertex AI User** role, on a project with the Vertex AI API enabled
- Chrome (or another modern browser) with a microphone

## First-time setup

```bash
cd /Users/kartik/Documents/Work/Projects/AI/kb_brain/GoogleLive
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional: check the key works:

```bash
export SSL_CERT_FILE=$(python -m certifi)
python hello_gemini.py          # text model hello world
python hello_gemini.py --live   # checks Gemini Live (voice) access
```

## Start the server

```bash
cd /Users/kartik/Documents/Work/Projects/AI/kb_brain/GoogleLive
source .venv/bin/activate
export SSL_CERT_FILE=$(python -m certifi)
uvicorn server:app --port 8080
```

Open **http://localhost:8080** and allow microphone access when asked.

> Port 8000 may already be in use on this machine, so 8080 is used here. Any free port works.

## Run a call

1. Pick a call script on the left (e.g. *ILP · Face-to-face · Financial review check*).
   The line pre-connects in the background, so the call starts instantly.
2. Click **Start call**. Mei Ling introduces herself straight away. Answer as the client.
   You can speak English or Mandarin; she follows your language.
3. Click **End call** (or let her finish). The checklist on the right fills in live and
   the outcome (PASS / REVIEW / ESCALATE, mis-selling suspected or not) is shown at the end.
4. The recording appears under **Recorded calls** for replay.

## Stop the server

Press `Ctrl+C` in the terminal running uvicorn, or from another terminal:

```bash
pkill -f "uvicorn server:app"
```

## Where things are saved

| Folder | Contents |
|---|---|
| `calls/<call_id>/` | `audio.webm` (both voices) + `meta.json` (transcript, answers, outcome) |
| `responses/` | Final compliance result JSON per completed call |
| `surveys/scripts/` | Call scripts (one JSON per scenario) |
| `surveys/blocks.json` | Reusable question blocks shared by the scripts |

For a clean demo, empty `calls/` and `responses/` beforehand.

## Optional settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_LIVE_MODEL` | `gemini-live-2.5-flash-native-audio` | Live voice model |
| `GEMINI_LIVE_LOCATION` | `us-central1` | Vertex region (live models are not on `global`) |
| `GEMINI_VOICE` | `Aoede` | Prebuilt female voice |
| `MAX_SESSION_SECONDS` | `900` | Hard cap on one call (cost guard) |
| `WARM_LINE_SECONDS` | `300` | How long a pre-connected line waits for Start |

## Troubleshooting

- **SSL / certificate errors**: make sure you ran `export SSL_CERT_FILE=$(python -m certifi)`.
- **403 / permission denied**: the service account needs the Vertex AI User role and the
  Vertex AI API must be enabled on the project.
- **"Address already in use"**: pick another port, e.g. `--port 8090`.
- **No audio from the bot / mic not working**: use `http://localhost` (not an IP address) so the
  browser allows the microphone, and check the site's mic permission.
