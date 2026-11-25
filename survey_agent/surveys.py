"""Load call-back survey scripts: reusable question blocks + per-script profile/sections."""

import json
from collections import defaultdict

import config

BLOCKS_PATH = config.SURVEYS_DIR / "blocks.json"
SCRIPTS_DIR = config.SURVEYS_DIR / "scripts"
DEFAULT_ID = "ilp_f2f_missell"


def _fmt(text: str, profile: dict) -> str:
    return text.format_map(defaultdict(lambda: "[unknown]", profile))


def load_all() -> dict[str, dict]:
    scripts = (json.loads(p.read_text()) for p in sorted(SCRIPTS_DIR.glob("*.json")))
    return {s["id"]: s for s in scripts}


def get(survey_id: str | None) -> dict:
    scripts = load_all()
    return scripts.get(survey_id or DEFAULT_ID) or scripts[DEFAULT_ID]


def sections(survey: dict) -> list[dict]:
    """Resolve block references into concrete sections with profile values filled in."""
    blocks = json.loads(BLOCKS_PATH.read_text())
    profile = survey["profile"]
    out = []
    for sec in survey["sections"]:
        block = blocks[sec] if isinstance(sec, str) else sec
        out.append(
            {
                "name": block["name"],
                "questions": [
                    {**q, **{k: _fmt(q[k], profile) for k in ("text", "check", "red_flag_if") if k in q}}
                    for q in block["questions"]
                ],
            }
        )
    return out


def questions(survey: dict) -> list[dict]:
    return [q for s in sections(survey) for q in s["questions"]]
