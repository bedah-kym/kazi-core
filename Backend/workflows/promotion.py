"""Promotion pipeline: repeated sessions -> skill candidate -> WorkflowDraft (#156).

Two entry points, one destination:

- **Explicit** — *"save what we just did as a skill"* packages the last agent
  session's tool sequence into a candidate, drafts it, writes a ``staging``
  skill folder and a ``WorkflowDraft``. Only a human confirmation creates the
  live ``UserWorkflow``.
- **Statistical** — telemetry mining finds repeated successful tool sequences
  and queues ``WorkflowCandidate`` rows. Drafting is a separate, explicit step.

Nothing in this module ever creates an active workflow.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from orchestration.telemetry import telemetry_path

from .capabilities import get_capabilities_prompt, validate_workflow_definition
from .models import WorkflowCandidate, WorkflowDraft
from .runtime import get_step_id, step_requires_approval

logger = logging.getLogger(__name__)

CONTRACT_SECTIONS = ("when_to_use", "inputs", "sequence", "validation", "returns", "approval")
_FAILURE_STATUSES = {"error", "failed", "timed_out", "rejected"}
_SECTION_TITLES = {
    "when_to_use": "When to use it",
    "inputs": "Required inputs and access",
    "sequence": "The sequence of work",
    "validation": "How to validate the result",
    "returns": "What to return",
    "approval": "What requires approval",
}
_SAVE_SKILL_PHRASES = (
    "save this as a skill",
    "save that as a skill",
    "save what we just did",
    "save it as a skill",
    "turn this into a skill",
    "turn that into a skill",
    "save this as a workflow",
    "turn this into a workflow",
)


class PromotionError(ValueError):
    """Raised when a promotion step cannot produce a reviewable draft."""


def slugify_skill_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:80] or "unnamed-skill"


def is_save_skill_request(message: str) -> bool:
    lowered = (message or "").strip().lower()
    return any(phrase in lowered for phrase in _SAVE_SKILL_PHRASES)


def extract_tool_sequence(transcript: Iterable[Dict[str, Any]]) -> List[str]:
    sequence = []
    for entry in transcript or []:
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool") or "").strip()
        if tool:
            sequence.append(tool)
    return sequence


def transcript_succeeded(transcript: Iterable[Dict[str, Any]]) -> bool:
    for entry in transcript or []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "").strip().lower()
        if status in _FAILURE_STATUSES:
            return False
    return True


def pattern_key(tool_sequence: Iterable[str]) -> str:
    payload = json.dumps({"tools": list(tool_sequence)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def mine_candidates(
    events: Iterable[Dict[str, Any]],
    *,
    min_occurrences: int = 3,
    min_success_rate: float = 0.8,
    min_steps: int = 2,
) -> List[Dict[str, Any]]:
    """Pure miner: ``agent_loop_done`` events -> repeated successful patterns."""
    tally: Dict[Any, Dict[str, Any]] = {}
    for event in events or []:
        if not isinstance(event, dict) or event.get("event") != "agent_loop_done":
            continue
        user_id = event.get("user_id")
        if not user_id:
            continue
        sequence = extract_tool_sequence(event.get("transcript") or [])
        if len(sequence) < min_steps:
            continue
        key = (user_id, event.get("room_id"), tuple(sequence))
        record = tally.setdefault(
            key,
            {"occurrences": 0, "successes": 0, "last_seen": None},
        )
        record["occurrences"] += 1
        if transcript_succeeded(event.get("transcript") or []):
            record["successes"] += 1
        record["last_seen"] = event.get("ts") or record["last_seen"]

    candidates: List[Dict[str, Any]] = []
    for (user_id, room_id, sequence), record in tally.items():
        occurrences = record["occurrences"]
        if occurrences < int(min_occurrences):
            continue
        success_rate = record["successes"] / occurrences if occurrences else 0.0
        if success_rate < float(min_success_rate):
            continue
        candidates.append({
            "user_id": user_id,
            "room_id": room_id,
            "tool_sequence": list(sequence),
            "occurrences": occurrences,
            "success_rate": round(success_rate, 4),
            "pattern_key": pattern_key(sequence),
        })
    candidates.sort(key=lambda c: (-c["occurrences"], c["pattern_key"]))
    return candidates


def read_telemetry_events(path: Optional[str] = None) -> List[Dict[str, Any]]:
    target = path or telemetry_path()
    events: List[Dict[str, Any]] = []
    try:
        with open(target, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        return []
    return events


def recent_agent_transcript(
    user_id: int,
    room_id: Optional[int] = None,
    *,
    path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The most recent agent-loop transcript for a user (optionally a room)."""
    events = read_telemetry_events(path)
    for event in reversed(events):
        if event.get("event") != "agent_loop_done":
            continue
        if event.get("user_id") != user_id:
            continue
        if room_id is not None and event.get("room_id") != room_id:
            continue
        transcript = event.get("transcript")
        if isinstance(transcript, list) and transcript:
            return transcript
    return []


def build_skill_contract(
    definition: Dict[str, Any],
    *,
    when_to_use: str = "",
    candidate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the six-part skill contract for a definition."""
    steps = [step for step in (definition or {}).get("steps", []) if isinstance(step, dict)]
    sequence = [
        f"{get_step_id(step, index)}: {step.get('service')} {step.get('action')}"
        for index, step in enumerate(steps)
    ]
    inputs = sorted({
        str(param)
        for step in steps
        for param in (step.get("params") or {})
        if param
    })
    approval_steps = [get_step_id(step, index) for index, step in enumerate(steps) if step_requires_approval(step)]
    services = sorted({str(step.get("service")) for step in steps if step.get("service")})
    return {
        "when_to_use": when_to_use or (candidate or {}).get("label") or (
            (definition or {}).get("workflow_description") or "When the task matches this repeated session."
        ),
        "inputs": inputs or ["No external inputs recorded; review before enabling."],
        "sequence": sequence,
        "validation": (
            "Drafted definition passes workflow validation. Re-run the test run before enabling "
            "under a standing grant."
        ),
        "returns": f"Results from: {', '.join(services) or 'no services'}.",
        "approval": (
            f"Steps requiring approval: {', '.join(approval_steps)}."
            if approval_steps
            else "No step needs approval by default; sends/purchases/deletes/publishes still gate."
        ),
    }


def render_skill_markdown(
    skill_name: str,
    description: str,
    tools: Iterable[str],
    contract: Dict[str, Any],
) -> str:
    tool_list = ", ".join(sorted({str(tool) for tool in tools if tool}))
    lines = [
        "---",
        f"name: {slugify_skill_name(skill_name)}",
        f"description: {description or 'Staged skill drafted by Kazi'}",
        f"tools: {tool_list}",
        "stage: staging",
        "---",
        "",
        f"# {skill_name}",
        "",
    ]
    for section in CONTRACT_SECTIONS:
        lines.append(f"## {_SECTION_TITLES[section]}")
        value = contract.get(section)
        if isinstance(value, list):
            for item in value:
                lines.append(f"- {item}")
        else:
            lines.append(str(value or ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _contained_skill_folder(root: Path, skill_name: str) -> Path:
    """Resolve a skill folder and prove it stays under the skills root.

    Two layers: ``os.path.basename`` drops any path structure from the slug,
    and the resolved candidate must sit directly under the resolved root.
    """
    root_resolved = Path(root).resolve()
    safe_name = os.path.basename(slugify_skill_name(skill_name))
    candidate = Path(os.path.abspath(os.path.join(str(root_resolved), safe_name)))  # codeql[py/path-injection]
    if candidate.parent != root_resolved:
        raise PromotionError("Invalid skill folder name.")
    return candidate


def write_staged_skill(
    skill_name: str,
    description: str,
    tools: Iterable[str],
    contract: Dict[str, Any],
    *,
    skills_root_override: Optional[Path] = None,
) -> Path:
    """Write a ``stage: staging`` skill folder; refuse to clobber a promoted one."""
    from orchestration.skill_registry import skills_root

    root = Path(skills_root_override) if skills_root_override else skills_root()
    if root is None:
        raise PromotionError("No skills directory is configured; cannot stage a skill.")

    folder = _contained_skill_folder(root, skill_name)
    skill_md = folder / "SKILL.md"
    if skill_md.exists():  # codeql[py/path-injection] — folder is contained under the resolved skills root
        stage = _read_stage(skill_md)
        if stage != "staging":
            raise PromotionError(
                f"Skill '{folder.name}' is already at stage '{stage}'; editing a promoted skill needs a human."
            )

    folder.mkdir(parents=True, exist_ok=True)  # codeql[py/path-injection] — contained folder
    skill_md.write_text(  # codeql[py/path-injection] — contained folder
        render_skill_markdown(skill_name, description, tools, contract),
        encoding="utf-8",
    )
    return skill_md


def _read_stage(skill_md: Path) -> str:
    try:
        raw = skill_md.read_text(encoding="utf-8")  # codeql[py/path-injection] — contained folder
    except OSError:
        return "unknown"
    match = re.search(r"^stage:\s*(\w+)\s*$", raw, re.MULTILINE)
    return match.group(1).lower() if match else "unknown"


def _draft_prompt(candidate: Dict[str, Any]) -> str:
    sequence = candidate.get("tool_sequence") or []
    return "\n".join([
        "Draft a workflow definition from this repeated session.",
        f"Tools actually used, in order: {', '.join(sequence)}",
        f"Occurrences observed: {candidate.get('occurrences', 0)}",
        "Follow the trust doctrine: prepare/draft before execute; sends, purchases, deletes,",
        "publishes and production changes stay behind approval.",
        "Include the optional capabilities manifest covering every step.",
        "Return JSON only in the documented workflow_definition shape.",
    ])


async def draft_workflow_from_candidate(
    candidate: Dict[str, Any],
    *,
    llm=None,
    source: str = "statistical",
    status: str = "draft",
    skills_root_override: Optional[Path] = None,
) -> WorkflowDraft:
    """LLM-draft a candidate into a staged skill + reviewable ``WorkflowDraft``."""
    user_id = candidate.get("user_id")
    if not user_id:
        raise PromotionError("Candidate has no user to draft for.")

    if llm is None:
        from orchestration.llm_client import get_llm_client

        llm = get_llm_client()

    response = await llm.generate_text(
        system_prompt=get_capabilities_prompt(),
        user_prompt=_draft_prompt(candidate),
        temperature=0.2,
        max_tokens=1200,
        json_mode=True,
    )
    parsed = llm.extract_json(response) or {}
    definition = parsed.get("workflow_definition") or parsed.get("definition")
    if not isinstance(definition, dict):
        raise PromotionError("The drafter did not return a workflow definition.")

    valid, error = validate_workflow_definition(definition)
    if not valid:
        raise PromotionError(f"Drafted definition failed validation: {error}")

    name = definition.get("workflow_name") or "Promoted skill"
    contract = build_skill_contract(definition, candidate=candidate)
    tools = candidate.get("tool_sequence") or []
    skill_md = write_staged_skill(
        name,
        definition.get("workflow_description") or "",
        tools,
        contract,
        skills_root_override=skills_root_override,
    )

    from asgiref.sync import sync_to_async

    owner_persona = None
    if candidate.get("room_id"):
        from orchestration.personas import resolve_room_persona

        owner_persona = await sync_to_async(resolve_room_persona)(
            candidate.get("room_id"), user_id
        )

    draft = await sync_to_async(WorkflowDraft.objects.create)(
        user_id=user_id,
        room_id=candidate.get("room_id"),
        definition=definition,
        status=status,
        source=source,
        skill_name=slugify_skill_name(name),
        skill_contract=contract,
        owner_persona=owner_persona,
    )
    logger.info("Staged skill %s (%s) as draft %s", draft.skill_name, skill_md, draft.id)
    return draft


async def save_session_as_skill(
    user_id: int,
    room_id: Optional[int],
    *,
    llm=None,
    skills_root_override: Optional[Path] = None,
) -> WorkflowDraft:
    """Explicit path: *"save what we just did as a skill."*"""
    transcript = recent_agent_transcript(user_id, room_id)
    if not transcript:
        raise PromotionError("I don't have a recent session to save as a skill yet.")

    candidate = {
        "user_id": user_id,
        "room_id": room_id,
        "tool_sequence": extract_tool_sequence(transcript),
        "occurrences": 1,
        "success_rate": 1.0 if transcript_succeeded(transcript) else 0.0,
        "label": "Saved from a session the user explicitly asked to keep.",
    }

    return await draft_workflow_from_candidate(
        candidate,
        llm=llm,
        source="explicit_save",
        status="awaiting_confirmation",
        skills_root_override=skills_root_override,
    )


def create_skill_from_text(
    *,
    name: str,
    description: str = "",
    tools: Optional[Iterable[str]] = None,
    contract: Optional[Dict[str, Any]] = None,
    user_id: int,
    room_id: Optional[int] = None,
):
    """Chat-authored skill -> staged skill folder + reviewable draft.

    Never active: a human promotes it from the Skills dashboard (or via
    ``skill_registry.transition_skill``). This is the write path behind the
    agent's ``save_skill`` meta-tool.
    """
    normalized = {
        section: str((contract or {}).get(section) or "")
        for section in CONTRACT_SECTIONS
    }
    path = write_staged_skill(
        name,
        description,
        [str(tool).strip() for tool in (tools or []) if str(tool).strip()],
        normalized,
    )

    resolved_room_id = None
    if room_id:
        try:
            from chatbot.models import Chatroom

            resolved_room_id = room_id if Chatroom.objects.filter(id=room_id).exists() else None
        except Exception:
            resolved_room_id = None

    draft = WorkflowDraft.objects.create(
        user_id=user_id,
        room_id=resolved_room_id,
        definition=None,
        status="draft",
        source="explicit_save",
        skill_name=slugify_skill_name(name),
        skill_contract=normalized,
    )
    return path, draft


def queue_candidates(
    candidates: Iterable[Dict[str, Any]],
) -> Dict[str, int]:
    """Persist mined candidates, skipping ones already queued for the user."""
    created = 0
    existing = 0
    for candidate in candidates:
        user_id = candidate.get("user_id")
        key = candidate.get("pattern_key")
        if not user_id or not key:
            continue
        if not _user_exists(user_id):
            continue
        _, was_created = WorkflowCandidate.objects.get_or_create(
            user_id=user_id,
            pattern_key=key,
            status="candidate",
            defaults={
                "room_id": candidate.get("room_id"),
                "pattern": {"tool_sequence": candidate.get("tool_sequence") or []},
                "occurrences": candidate.get("occurrences") or 0,
                "success_rate": candidate.get("success_rate") or 0.0,
            },
        )
        if was_created:
            created += 1
        else:
            existing += 1
    return {"created": created, "existing": existing}


def _user_exists(user_id: int) -> bool:
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(id=user_id).exists()
