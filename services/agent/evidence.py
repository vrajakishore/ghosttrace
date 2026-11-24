"""Evidence pack for a human reviewer.

The default text comes from a fixed template. `write_foundry_pack` optionally asks a Foundry
prompt agent instead. The grounding check below applies to both, because it is the
safeguard that matters: a pack that mentions a registration number, toll point or time
that is not in the case data is rejected.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from services.common.config import ROOT
from services.common.llm import ModelAnswerError, complete_prompt_agent_json, is_foundry_agent_enabled, prompt_agent_name

VRN_RE = re.compile(r"\bXX\d{2}[A-Z]{2}\d{6}\b")
PLAZA_RE = re.compile(r"\b[LM]\d\b")
TIME_RE = re.compile(r"\bDay \d+, \d{2}:\d{2}\b")
PENALTY_RE = re.compile(r"\b(fine|fined|penalt\w*|offen[cs]e|guilty|prosecut\w*|punish\w*)\b", re.I)
IMAGE_CLAIM_RE = re.compile(r"\b(?:photo|photograph)\b|\bimage\b(?![- ]similarity\b)", re.I)
AGENT_SCHEMA = ROOT / "agents" / "prompt" / "explanation.schema.json"
logger = logging.getLogger("ghosttrace.evidence")


def grounding_check(text: str, case: dict) -> list[str]:
    """Return every registration number, toll point and time in `text` that the case data does not contain."""
    source = json.dumps({k: v for k, v in case.items() if k != "evidence_pack"})
    bad = []
    for pattern in (VRN_RE, PLAZA_RE, TIME_RE):
        allowed = set(pattern.findall(source))
        bad += [item for item in pattern.findall(text) if item not in allowed]
    return sorted(set(bad))


def write_pack(case: dict) -> dict:
    g, s = case["ghost"], case["settings"]
    o = g["observed"]
    passing = [c for c in case["candidates"] if c["passes"]]
    in_window = [c for c in case["candidates"] if c["in_window"]]
    lines = [f"What happened. At {g['time']} a vehicle crossed {g['plaza_id']} {g['direction']} and was not identified "
             f"(plate status: {g['plate_status'].replace('_', ' ')}). The camera described it as a {o['colour']} "
             f"{o['make']} {o['model']} ({o['body'].replace('_', ' ')}).",
             f"What was searched. Identified crossings at legacy plazas, same direction, within a "
             f"{s['window']:.0f}-minute window around the expected travel time, registered with the same make, "
             f"model, colour and body. {len(in_window)} registered vehicle(s) fit."]
    if case["decision"] == "recommend" and passing:
        t = passing[0]
        others = [c for c in in_window if c["vrn"] != t["vrn"]]
        lines.append(f"Result. One vehicle passes the image check: {t['vrn']}, identified at {t['plaza_id']} at "
                     f"{t['time']} ({t['travel_minutes']:.0f} minutes of travel, {abs(t['offset_minutes']):.0f} minutes "
                     f"{'later' if t['offset_minutes'] > 0 else 'earlier'} than expected). Image similarity "
                     f"{t['image_similarity']:.2f}; threshold {s['threshold']:.2f}."
                     + (f" The next closest vehicle scores {others[0]['image_similarity']:.2f}." if others else
                        " No other vehicle of this description was in the window."))
        lines.append("Recommendation: RECOMMEND for human review. This is not a penalty.")
    elif case["decision"] == "review":
        names = ", ".join(f"{c['vrn']} ({c['image_similarity']:.2f})" for c in passing[:5])
        lines.append(f"Result. More than one vehicle is plausible, or the best one does not clearly beat the next: {names}.")
        lines.append("Recommendation: REVIEW. Do not act without more evidence.")
    else:
        lines.append("Result. No vehicle passes the image check"
                     + (f" (best similarity {in_window[0]['image_similarity']:.2f}, threshold {s['threshold']:.2f})."
                        if in_window else ", and no vehicle of this description was in the window."))
        lines.append("Recommendation: UNRESOLVED. The vehicle may have left the corridor before a legacy plaza, "
                     "or the camera may have misread its model or colour.")
    if g["plate_status"] == "glare_weather":
        lines.append("Note. The plate status points to an honest read failure (glare or weather), not evasion. "
                     "If the reviewer agrees, the right outcome is an ordinary toll notice.")
    lines.append("Reviewer checks. Compare the observed and registered make, model, colour and body. Check the travel-time "
                 "offset and simulated image-similarity score. Verify the simulated plate status in the case data. "
                 "No real images were processed.")
    text = "\n\n".join(lines)
    bad = grounding_check(text, case)
    return {"text": text, "grounding_ok": not bad, "ungrounded_items": bad, "source": "template"}


def write_foundry_pack(case: dict) -> dict:
    """Generate an optional Foundry explanation; use the template unless every gate passes."""
    fallback = write_pack(case)

    def use_template(reason: str) -> dict:
        logger.info("Foundry explanation fallback reason=%s", reason)
        return {**fallback, "source": "template (fallback)"}

    if not is_foundry_agent_enabled():
        return fallback

    agent_case = {key: value for key, value in case.items() if key not in ("evidence_pack", "review")}
    try:
        answer = complete_prompt_agent_json(agent_case, AGENT_SCHEMA)
    except ModelAnswerError:
        return use_template("model_call_failed")

    expected_vrn = case.get("top_vrn") if case.get("decision") == "recommend" else None
    if answer.get("decision") != case.get("decision") or answer.get("named_vrn") != expected_vrn:
        return use_template("decision_mismatch")

    narrative = [answer["summary"], *answer["reasons"], *answer["reviewer_checks"]]
    prose = " ".join(str(part) for part in narrative)
    text = "\n\n".join([answer["summary"], *answer["reasons"],
                         "Reviewer checks. " + " ".join(answer["reviewer_checks"]), answer["notice"]])
    grounded = grounding_check(text, case)
    unavailable_image_claim = IMAGE_CLAIM_RE.search(prose)
    if (not answer["summary"].strip() or not answer["reasons"] or not answer["reviewer_checks"]
            or len(text.split()) > 200 or grounded or PENALTY_RE.search(prose) or unavailable_image_claim
            or (case.get("ghost", {}).get("plate_status") == "glare_weather"
                and not answer.get("honest_failure_possible"))):
        return use_template("content_check_failed")

    return {"text": text, "grounding_ok": True, "ungrounded_items": [], "source": prompt_agent_name()}
