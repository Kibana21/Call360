"""Tools the call-back agent uses to record answers and close out the verification."""

import json
from datetime import datetime, timezone

from google.adk.tools import ToolContext

import config

from . import surveys

FLAGS = ("ok", "concern", "red_flag")


def _survey(tool_context: ToolContext) -> dict:
    # adk web sessions have no survey_id in state -> default script.
    return surveys.get(tool_context.state.get("survey_id"))


def _remaining(survey: dict, answers: dict) -> list[str]:
    return [q["id"] for q in surveys.questions(survey) if q["id"] not in answers]


def record_answer(question_id: str, answer: str, flag: str, note: str, tool_context: ToolContext) -> dict:
    """Record the client's answer to one verification question, with a compliance flag.

    Call as soon as the answer is clear. Calling again for the same question
    overwrites it (use when the client corrects themselves).

    Args:
        question_id: Id of the question (e.g. "fna_discussion").
        answer: Short, faithful summary of what the client said.
        flag: "ok" if the answer meets the check, "concern" if partially or
            unclear, "red_flag" if it matches the red-flag criteria.
        note: One short sentence explaining the flag (empty string if ok).

    Returns:
        Status, the next unanswered question id, and whether all are done.
    """
    survey = _survey(tool_context)
    qids = [q["id"] for q in surveys.questions(survey)]
    if question_id not in qids:
        return {"status": "error", "message": f"Unknown question_id. Valid ids: {qids}"}
    if flag not in FLAGS:
        flag = "concern"

    answers = dict(tool_context.state.get("answers", {}))
    prev = answers.get(question_id)
    if prev and prev["answer"] == answer and prev["flag"] == flag:
        # Guard against the model re-calling the tool for an answer it already recorded.
        remaining = _remaining(survey, answers)
        return {
            "status": "already_recorded",
            "instruction": "Do not record this again. Speak to the client now"
            + (f" and ask about {remaining[0]}." if remaining else " and close the call."),
            "next_question_id": remaining[0] if remaining else None,
            "all_done": not remaining,
        }
    answers[question_id] = {
        "answer": answer,
        "flag": flag,
        "note": note,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    tool_context.state["answers"] = answers
    remaining = _remaining(survey, answers)
    return {
        "status": "recorded",
        "next_question_id": remaining[0] if remaining else None,
        "remaining_count": len(remaining),
        "all_done": not remaining,
        "instruction": "Continue the conversation. Do not repeat anything you have already said; "
        "if you already asked the next question, just wait for the client's answer.",
    }


def finish_survey(summary: str, missell_assessment: str, tool_context: ToolContext) -> dict:
    """Close the verification call and save the result. Call once, after saying goodbye.

    Args:
        summary: Two or three sentence summary of the call for the compliance team.
        missell_assessment: Your judgement of whether the policy may have been
            mis-sold (e.g. no proper financial needs review, unsuitable product,
            guaranteed-return claims), with the key reasons.

    Returns:
        The outcome (PASS / REVIEW / ESCALATE) and where it was saved.
    """
    # Idempotent: the live model sometimes re-calls this; save only once per call.
    if tool_context.state.get("final_result"):
        return {
            **tool_context.state["final_result"],
            "status": "already_saved",
            "instruction": "The survey is already saved. Do not call finish_survey again. "
            "If you haven't yet, say a short goodbye; otherwise stay silent.",
        }

    survey = _survey(tool_context)
    qs = {q["id"]: q for q in surveys.questions(survey)}
    answers = tool_context.state.get("answers", {})

    red = [qid for qid, a in answers.items() if a["flag"] == "red_flag"]
    concerns = [qid for qid, a in answers.items() if a["flag"] == "concern"]
    missell_hits = [qid for qid in red if qs.get(qid, {}).get("missell")]
    missell_concerns = [qid for qid in concerns if qs.get(qid, {}).get("missell")]
    unanswered = _remaining(survey, answers)

    if red:
        outcome = "ESCALATE"
    elif concerns or unanswered:
        outcome = "REVIEW"
    else:
        outcome = "PASS"
    missell_suspected = bool(missell_hits) or len(missell_concerns) >= 2

    result = {
        "survey_id": survey["id"],
        "survey_title": survey["title"],
        "client": survey["profile"],
        "session_id": tool_context.session.id if tool_context.session else "unknown",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "missell_suspected": missell_suspected,
        "missell_indicators": missell_hits + missell_concerns,
        "red_flags": red,
        "concerns": concerns,
        "unanswered": unanswered,
        "agent_summary": summary,
        "missell_assessment": missell_assessment,
        "answers": answers,
    }
    config.RESPONSES_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    path = config.RESPONSES_DIR / f"{survey['id']}_{now:%Y%m%d_%H%M%S}_{result['session_id'][:8]}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    summary_result = {
        "status": "saved",
        "path": path.name,
        "outcome": outcome,
        "missell_suspected": missell_suspected,
        "red_flags": red,
        "concerns": concerns,
    }
    tool_context.state["final_result"] = summary_result
    return summary_result
