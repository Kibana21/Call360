"""FastAPI server bridging a browser mic/speaker to an ADK live (bidi-streaming) call-back agent.

Each call is saved under calls/<call_id>/:
  meta.json   script, client, timings, full transcript and the compliance result (written by server)
  audio.webm  mixed recording of both sides (uploaded by the browser when the call ends)

Run:  .venv/bin/uvicorn server:app --port 8080   then open http://localhost:8080
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone

import config  # noqa: F401  (must come before google.adk imports)
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from survey_agent import surveys
from survey_agent.agent import build_agent

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("callback_bot")

APP_NAME = "callback_bot"
STATIC_DIR = config.ROOT / "static"
CALLS_DIR = config.ROOT / "calls"
CALLS_DIR.mkdir(exist_ok=True)
CALL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
MAX_AUDIO_BYTES = 200 * 1024 * 1024

session_service = InMemorySessionService()

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/calls", StaticFiles(directory=CALLS_DIR), name="calls")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/surveys")
async def list_surveys() -> list[dict]:
    out = []
    for s in surveys.load_all().values():
        out.append(
            {
                "id": s["id"],
                "title": s["title"],
                "product": s["product"],
                "channel": s["channel"],
                "tags": s.get("tags", []),
                "focus": s["focus"],
                "profile": s["profile"],
                "roleplay_tips": s.get("roleplay_tips", []),
                "sections": [
                    {
                        "name": sec["name"],
                        "questions": [
                            {"id": q["id"], "text": q["text"], "missell": bool(q.get("missell"))}
                            for q in sec["questions"]
                        ],
                    }
                    for sec in surveys.sections(s)
                ],
            }
        )
    return out


@app.get("/api/calls")
async def list_calls() -> list[dict]:
    """Recorded calls, newest first."""
    out = []
    for meta_path in sorted(CALLS_DIR.glob("*/meta.json"), reverse=True):
        try:
            m = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        audio = meta_path.parent / "audio.webm"
        out.append(
            {
                "call_id": m["call_id"],
                "survey_title": m["survey_title"],
                "client_name": m["client_name"],
                "started_at": m["started_at"],
                "duration_s": m.get("duration_s"),
                "outcome": (m.get("result") or {}).get("outcome"),
                "missell_suspected": (m.get("result") or {}).get("missell_suspected"),
                "audio_url": f"/calls/{m['call_id']}/audio.webm" if audio.exists() else None,
            }
        )
    return out


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str) -> dict:
    if not CALL_ID_RE.match(call_id):
        raise HTTPException(400, "bad call id")
    path = CALLS_DIR / call_id / "meta.json"
    if not path.exists():
        raise HTTPException(404, "not found")
    m = json.loads(path.read_text())
    audio = CALLS_DIR / call_id / "audio.webm"
    m["audio_url"] = f"/calls/{call_id}/audio.webm" if audio.exists() else None
    return m


@app.post("/api/calls/{call_id}/audio")
async def upload_audio(call_id: str, request: Request) -> dict:
    if not CALL_ID_RE.match(call_id) or not (CALLS_DIR / call_id).is_dir():
        raise HTTPException(400, "unknown call id")
    data = await request.body()
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(400, "empty or too large")
    (CALLS_DIR / call_id / "audio.webm").write_bytes(data)
    log.info("call %s audio saved (%d KB)", call_id, len(data) // 1024)
    return {"status": "saved", "url": f"/calls/{call_id}/audio.webm"}


def make_run_config() -> RunConfig:
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.VOICE)
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Less trigger-happy voice detection: background noise / echo shouldn't cut Mei Ling off,
        # and short pauses mid-sentence shouldn't end the client's turn.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                prefix_padding_ms=200,
                silence_duration_ms=700,
            )
        ),
    )


KICKOFF = "(Call connected. The client has picked up: begin the call.)"


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, survey: str | None = None) -> None:
    """One websocket = one pre-connected line.

    The Gemini Live session is opened as soon as the browser connects (when a script is
    selected), so pressing Start only has to send the kickoff and Mei Ling speaks almost
    immediately. The call is recorded from {"type": "start"} until stop/disconnect.
    """
    await websocket.accept()
    script = surveys.get(survey)
    runner = Runner(app_name=APP_NAME, agent=build_agent(script), session_service=session_service)
    user_id = "web_user"
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=str(uuid.uuid4()),
        state={"survey_id": script["id"]},
    )
    queue = LiveRequestQueue()
    started = asyncio.Event()
    call: dict = {}  # filled in on start
    transcript: list[dict] = []
    answers: list[dict] = []
    result: dict = {}

    def save_meta() -> None:
        if not call:
            return
        ended = datetime.now(timezone.utc)
        meta = {
            "call_id": call["id"],
            "survey_id": script["id"],
            "survey_title": script["title"],
            "client_name": script["profile"]["client_name"],
            "profile": script["profile"],
            "started_at": call["started"].isoformat(),
            "ended_at": ended.isoformat(),
            "duration_s": round((ended - call["started"]).total_seconds()),
            "transcript": transcript,
            "answers": answers,
            "result": result,
        }
        (call["dir"] / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    def add_line(who: str, text: str) -> None:
        if call:
            offset = round((datetime.now(timezone.utc) - call["started"]).total_seconds(), 1)
            transcript.append({"t": offset, "speaker": who, "text": text.strip()})

    async def begin_call() -> None:
        now = datetime.now(timezone.utc)
        call_id = f"{now:%Y%m%d_%H%M%S}_{script['id']}_{session.id[:6]}"
        call.update(id=call_id, started=now, dir=CALLS_DIR / call_id)
        call["dir"].mkdir(parents=True, exist_ok=True)
        save_meta()
        queue.send_content(types.Content(role="user", parts=[types.Part(text=KICKOFF)]))
        started.set()
        log.info("call %s started", call_id)
        await websocket.send_json({"type": "session", "call_id": call_id})

    async def upstream() -> None:
        """Browser -> agent: control messages + raw 16 kHz PCM16 mono frames (only once started)."""
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes"):
                if started.is_set():
                    queue.send_realtime(types.Blob(mime_type="audio/pcm;rate=16000", data=msg["bytes"]))
            elif msg.get("text"):
                kind = json.loads(msg["text"]).get("type")
                if kind == "start" and not started.is_set():
                    await begin_call()
                elif kind == "stop":
                    return

    async def downstream() -> None:
        """Agent -> browser: 24 kHz PCM audio (binary) + JSON events (text)."""
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session.id,
            live_request_queue=queue,
            run_config=make_run_config(),
        ):
            if event.interrupted:
                await websocket.send_json({"type": "interrupted"})
            for who, tr in (("user", event.input_transcription), ("bot", event.output_transcription)):
                if tr and tr.text:
                    await websocket.send_json({"type": who, "text": tr.text, "final": bool(tr.finished)})
                    if tr.finished:
                        add_line("client" if who == "user" else "agent", tr.text)
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.inline_data and part.inline_data.data:
                        await websocket.send_bytes(part.inline_data.data)
                    fc = part.function_call
                    if fc and fc.name == "record_answer":
                        args = dict(fc.args or {})
                        answers.append(args)
                        await websocket.send_json({"type": "answer", **args})
                    fr = part.function_response
                    if fr and fr.name == "finish_survey":
                        result.update(fr.response or {})
                        save_meta()
                        await websocket.send_json({"type": "done", "result": fr.response})
            if event.turn_complete:
                await websocket.send_json({"type": "turn_complete"})

    async def watchdog() -> None:
        """Close idle warm lines, and cap call length."""
        try:
            await asyncio.wait_for(started.wait(), config.WARM_LINE_SECONDS)
        except asyncio.TimeoutError:
            log.info("warm line for %s idle, closing", script["id"])
            return
        await asyncio.sleep(config.MAX_SESSION_SECONDS)
        log.info("call %s hit time limit", call.get("id"))

    await websocket.send_json({"type": "ready", "survey_id": script["id"]})
    tasks = [asyncio.create_task(c()) for c in (upstream, downstream, watchdog)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if not t.cancelled() and t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                log.error("line %s error: %r", script["id"], t.exception())
                try:
                    await websocket.send_json({"type": "error", "text": str(t.exception())})
                except Exception:
                    pass
    finally:
        queue.close()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        save_meta()
        try:
            await websocket.close()
        except Exception:
            pass
        if call:
            log.info("call %s closed (%d transcript lines)", call["id"], len(transcript))
