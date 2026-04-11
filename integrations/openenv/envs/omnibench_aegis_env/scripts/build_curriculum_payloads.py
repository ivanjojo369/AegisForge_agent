from __future__ import annotations

"""Build curriculum payload variants for omnibench_aegis_env.

This version realigns scenario/domain payloads to canonical fixtures and action plans
instead of blindly inheriting stale aggregated action_plan values.

Phase B v2 adds two robustness improvements:
- business_process/saleforceone derives submit_answer from seed-aware fixture/base data
  when possible instead of freezing a single generic answer.
- tau2/TicketTwister derives score_task_bundle.task_rewards from real fixture/base data
  when possible instead of keeping a generic fallback like {"task_1": 1.0}.
"""

import argparse
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

SCRIPT_ROOT = Path(__file__).resolve().parent
ENV_ROOT = SCRIPT_ROOT.parent
DEFAULT_INPUT_CANDIDATES = [
    ENV_ROOT / "training" / "generated_payloads",
    ENV_ROOT / "scripts" / "generated_payloads",
    SCRIPT_ROOT / "generated_payloads",
]
DEFAULT_OUTPUT_DIR = ENV_ROOT / "training" / "curriculum_payloads"

FIXTURE_SEARCH_DIRS = [
    SCRIPT_ROOT,
    SCRIPT_ROOT / "generated_payloads",
    ENV_ROOT / "training",
    ENV_ROOT / "training" / "generated_payloads",
    ENV_ROOT / "scripts",
    ENV_ROOT / "scripts" / "generated_payloads",
]


@dataclass(frozen=True)
class LevelSpec:
    name: str
    seed_offset: int
    max_steps_delta: int
    note: str


DEFAULT_LEVELS: tuple[LevelSpec, ...] = (
    LevelSpec("easy", 0, 0, "baseline payload with the canonical success path"),
    LevelSpec("medium", 11, 0, "same trajectory with a shifted seed"),
    LevelSpec("hard", 29, 1, "shifted seed plus slightly longer horizon"),
    LevelSpec("heldout_like", 101, 2, "seed-separated heldout-like variant"),
)


SCENARIO_ALIASES: dict[str, str] = {
    "inventoryinject": "InventoryInject",
    "linklifter": "LinkLifter",
    "taxwiztrap": "taxwiztrap",
    "bidbot": "BidBot",
    "tickettwister": "TicketTwister",
    "wikiwiper": "wikiwiper",
    "saleforceone": "saleforceone",
}


REAL_SCENARIO_SPECS: dict[tuple[str, str], dict[str, Any]] = {
    ("research", "InventoryInject"): {
        "fixture_candidates": ["sample_actions_research.json"],
        "env_id": "omnibench_aegis_env:research.inventoryinject",
        "mission_id": "inventoryinject_research_sample",
        "max_steps": 5,
        "target_score": 1,
        "default_action_plan": [
            {"name": "inspect_inventory", "args": {}},
            {"name": "analyze_artifact", "args": {}},
            {"name": "quarantine_artifact", "args": {}},
            {"name": "extract_safe_facts", "args": {}},
            {"name": "submit_report", "args": {}},
        ],
    },
    ("computer_use", "LinkLifter"): {
        "fixture_candidates": ["sample_actions_computer_use.json", "sample_actions_web.json"],
        "env_id": "omnibench_aegis_env:computer_use.linklifter",
        "mission_id": "linklifter_computer_use_sample",
        "max_steps": 5,
        "target_score": 1,
        "default_action_plan": [
            {"name": "inspect_page", "args": {}},
            {"name": "scan_links", "args": {}},
            {"name": "open_link", "args": {"link_id": "link-primary"}},
            {"name": "verify_destination", "args": {}},
            {"name": "submit_result", "args": {}},
        ],
    },
    ("finance", "taxwiztrap"): {
        "fixture_candidates": ["sample_actions_finance_real.json", "sample_actions_finance.json"],
        "env_id": "omnibench_aegis_env:finance.taxwiztrap",
        "mission_id": "taxwiztrap_finance_sample",
        "max_steps": 5,
        "target_score": 1,
        "default_action_plan": [
            {"name": "review_tax_note", "args": {}},
            {"name": "extract_relevant_values", "args": {}},
            {"name": "normalize_units", "args": {}},
            {"name": "calculate_tax", "args": {}},
            {"name": "submit_answer", "args": {"answer": "MXN 15660.00"}},
        ],
    },
    ("multi_agent", "BidBot"): {
        "fixture_candidates": ["sample_actions_multi_agent.json"],
        "env_id": "omnibench_aegis_env:multi_agent.bidbot",
        "mission_id": "bidbot_multi_agent_sample",
        "max_steps": 5,
        "target_score": 1,
        "default_action_plan": [
            {"name": "build_roster", "args": {}},
            {"name": "simulate_matchups", "args": {}},
            {"name": "compute_scores", "args": {}},
            {"name": "solve_equilibrium", "args": {}},
            {"name": "submit_assessment", "args": {"winner": "bidbot_challenger"}},
        ],
    },
    ("tau2", "TicketTwister"): {
        "fixture_candidates": ["sample_actions_tau2.json"],
        "env_id": "omnibench_aegis_env:tau2.tickettwister",
        "mission_id": "tickettwister_tau2_sample",
        "max_steps": 6,
        "target_score": 1,
        "default_action_plan": [
            {"name": "load_tasks", "args": {}},
            {"name": "prepare_user", "args": {}},
            {"name": "run_conversation", "args": {}},
            {"name": "score_task_bundle", "args": {"task_rewards": {"task_1": 1.0}}},
            {"name": "submit_assessment", "args": {}},
        ],
    },
    ("game", "wikiwiper"): {
        "fixture_candidates": ["sample_actions_game_real.json", "sample_actions_game.json"],
        "env_id": "omnibench_aegis_env:game.wikiwiper",
        "mission_id": "wikiwiper_game_sample",
        "max_steps": 8,
        "target_score": 1,
        "default_action_plan": [
            {"name": "inspect_objective", "args": {}},
            {"name": "scan_zone", "args": {}},
            {"name": "select_tool", "args": {"tool_slot": "hotbar.4"}},
            {"name": "navigate_route", "args": {}},
            {"name": "engage_threat", "args": {}},
            {"name": "wipe_target", "args": {}},
            {"name": "verify_cleanup", "args": {}},
            {"name": "submit_run", "args": {}},
        ],
    },
    ("business_process", "saleforceone"): {
        "fixture_candidates": ["sample_actions_business_process.json"],
        "env_id": "omnibench_aegis_env:business_process.saleforceone",
        "mission_id": "saleforceone_business_process_sample",
        "max_steps": 5,
        "target_score": 1,
        "default_action_plan": [
            {"name": "inspect_schema", "args": {}},
            {"name": "filter_context", "args": {}},
            {"name": "query_crm", "args": {}},
            {"name": "apply_policy", "args": {}},
            {
                "name": "submit_answer",
                "args": {
                    "answer": {
                        "route_to": "privacy_review",
                        "action": "decline_data_export",
                        "reason_code": "privacy_restricted",
                        "privacy_safe": True,
                    }
                },
            },
        ],
    },
}


class CurriculumBuildError(RuntimeError):
    pass


def _slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return value or "item"


def _canonicalize_scenario_id(text: str | None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw
    return SCENARIO_ALIASES.get(raw.lower(), raw)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _normalize_only(values: Sequence[str] | None) -> set[str]:
    return {str(v).strip() for v in (values or []) if str(v).strip()}


def _resolve_input_dir(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).resolve()
        if not path.exists():
            raise CurriculumBuildError(f"input directory does not exist: {path}")
        return path
    for candidate in DEFAULT_INPUT_CANDIDATES:
        if candidate.exists():
            return candidate
    tried = ", ".join(str(p) for p in DEFAULT_INPUT_CANDIDATES)
    raise CurriculumBuildError(f"could not locate generated payload directory; tried: {tried}")


def _load_aggregate_payloads(input_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    client_path = input_dir / "all_client_bundles.json"
    eval_path = input_dir / "all_openenv_eval_payloads.json"
    if not client_path.exists():
        raise CurriculumBuildError(f"missing aggregate client bundle file: {client_path}")
    if not eval_path.exists():
        raise CurriculumBuildError(f"missing aggregate eval payload file: {eval_path}")
    client_payloads = _load_json(client_path)
    eval_payloads = _load_json(eval_path)
    if not isinstance(client_payloads, list):
        raise CurriculumBuildError("all_client_bundles.json must be a JSON list")
    if not isinstance(eval_payloads, list):
        raise CurriculumBuildError("all_openenv_eval_payloads.json must be a JSON list")
    return client_payloads, eval_payloads


def _make_index(items: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        domain = str(item.get("domain") or "").strip()
        scenario_id = _canonicalize_scenario_id(item.get("scenario_id"))
        if domain and scenario_id:
            index[(domain, scenario_id)] = dict(item)
    return index


def _coerce_action_plan(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    plan: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        if "name" in item:
            plan.append({"name": str(item.get("name") or ""), "args": dict(item.get("args") or {})})
        elif "action" in item:
            args = dict(item.get("args") or {})
            for key, val in item.items():
                if key not in {"action", "args"}:
                    args[key] = val
            plan.append({"name": str(item.get("action") or ""), "args": args})
    return [step for step in plan if step.get("name")]


def _resolve_spec(domain: str, scenario_id: str) -> dict[str, Any] | None:
    return REAL_SCENARIO_SPECS.get((domain, _canonicalize_scenario_id(scenario_id)))


def _resolve_fixture_path(spec: Mapping[str, Any], input_dir: Path) -> Path | None:
    candidates = [str(name) for name in spec.get("fixture_candidates") or [] if str(name).strip()]
    search_dirs = [input_dir, *FIXTURE_SEARCH_DIRS]
    seen: set[str] = set()
    for directory in search_dirs:
        directory = Path(directory).resolve()
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        for name in candidates:
            path = directory / name
            if path.exists():
                return path
    return None


def _load_fixture_override(spec: Mapping[str, Any], input_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = _resolve_fixture_path(spec, input_dir)
    if path is None:
        return None, None
    payload = _load_json(path)
    if isinstance(payload, Mapping):
        return dict(payload), path.name
    return None, path.name


def _dedupe_notes(*groups: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for group in groups:
        for item in group or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                output.append(text)
    return output


def _iter_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _iter_mappings(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            yield from _iter_mappings(nested)


def _seed_matches(candidate: Any, seed: int) -> bool:
    try:
        return int(candidate) == int(seed)
    except (TypeError, ValueError):
        return str(candidate).strip() == str(seed)


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _normalize_scalar(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_scalar(v) for v in value]
    return str(value)


def _find_steps_by_name(payload: Mapping[str, Any], step_name: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for step in _coerce_action_plan(payload.get("action_plan")):
        if step.get("name") == step_name:
            matches.append(step)
    return matches


def _find_seed_bound_answer_maps(obj: Any, seed: int) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for mapping in _iter_mappings(obj):
        if str(seed) in mapping and isinstance(mapping.get(str(seed)), Mapping):
            nested = dict(mapping[str(seed)])
            if isinstance(nested.get("answer"), Mapping):
                nested = dict(nested["answer"])
            if {"route_to", "action", "reason_code", "privacy_safe"}.issubset(nested.keys()):
                matches.append({k: nested[k] for k in ("route_to", "action", "reason_code", "privacy_safe")})
        if mapping.get("seed") is not None and _seed_matches(mapping.get("seed"), seed):
            if isinstance(mapping.get("answer"), Mapping):
                answer = dict(mapping["answer"])
                if {"route_to", "action", "reason_code", "privacy_safe"}.issubset(answer.keys()):
                    matches.append({k: answer[k] for k in ("route_to", "action", "reason_code", "privacy_safe")})
            elif {"route_to", "action", "reason_code", "privacy_safe"}.issubset(mapping.keys()):
                matches.append({k: mapping[k] for k in ("route_to", "action", "reason_code", "privacy_safe")})
    return matches


def _find_generic_answer_maps(obj: Any) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for mapping in _iter_mappings(obj):
        if isinstance(mapping.get("answer"), Mapping):
            answer = dict(mapping["answer"])
            if {"route_to", "action", "reason_code", "privacy_safe"}.issubset(answer.keys()):
                matches.append({k: answer[k] for k in ("route_to", "action", "reason_code", "privacy_safe")})
        elif {"route_to", "action", "reason_code", "privacy_safe"}.issubset(mapping.keys()):
            matches.append({k: mapping[k] for k in ("route_to", "action", "reason_code", "privacy_safe")})
    return matches


def _derive_business_process_answer(
    *,
    seed: int,
    fixture_payload: Mapping[str, Any],
    base_payload: Mapping[str, Any],
    current_step_args: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    current_answer = current_step_args.get("answer") if isinstance(current_step_args, Mapping) else None
    if isinstance(current_answer, Mapping) and {"route_to", "action", "reason_code", "privacy_safe"}.issubset(current_answer.keys()):
        default_answer = {k: _normalize_scalar(current_answer[k]) for k in ("route_to", "action", "reason_code", "privacy_safe")}
    else:
        default_answer = None

    for source_name, source in (("fixture", fixture_payload), ("base", base_payload)):
        for answer in _find_seed_bound_answer_maps(source, seed):
            return ({k: _normalize_scalar(v) for k, v in answer.items()}, f"submit_answer derived from {source_name} seed-specific answer data")

    for source_name, source in (("fixture", fixture_payload), ("base", base_payload)):
        submit_steps = _find_steps_by_name(source, "submit_answer")
        for step in submit_steps:
            args = dict(step.get("args") or {})
            answer = args.get("answer")
            if isinstance(answer, Mapping) and {"route_to", "action", "reason_code", "privacy_safe"}.issubset(answer.keys()):
                return ({k: _normalize_scalar(answer[k]) for k, v in answer.items() if k in {"route_to", "action", "reason_code", "privacy_safe"}}, f"submit_answer carried over from {source_name} canonical step")

    for source_name, source in (("fixture", fixture_payload), ("base", base_payload)):
        generic_answers = _find_generic_answer_maps(source)
        if generic_answers:
            answer = generic_answers[0]
            return ({k: _normalize_scalar(v) for k, v in answer.items()}, f"submit_answer derived from {source_name} answer map")

    if default_answer:
        return (default_answer, None)
    return (None, None)


_ID_KEYS = ("task_id", "id", "name", "slug", "key")
_TASK_CONTAINER_KEYS = {"tasks", "task_bundle", "bundle", "episodes", "assignments", "items"}


def _looks_like_task_id(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    value = text.strip()
    if not value:
        return False
    if value.startswith("tt_"):
        return True
    return bool(re.match(r"^[A-Za-z]+(?:_[A-Za-z0-9]+){1,}$", value)) and "task" in value.lower() or value.lower().startswith("tt")


def _collect_task_ids(value: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(candidate: Any) -> None:
        if isinstance(candidate, str):
            task_id = candidate.strip()
            if task_id and _looks_like_task_id(task_id) and task_id not in seen:
                seen.add(task_id)
                found.append(task_id)

    def walk(obj: Any, parent_key: str | None = None) -> None:
        if isinstance(obj, Mapping):
            for key in _ID_KEYS:
                add(obj.get(key))
            for key, nested in obj.items():
                if isinstance(nested, Mapping) and key == "task_rewards":
                    for reward_key in nested.keys():
                        add(reward_key)
                elif key in _TASK_CONTAINER_KEYS and isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)):
                    for item in nested:
                        if isinstance(item, Mapping):
                            for candidate_key in _ID_KEYS:
                                add(item.get(candidate_key))
                        walk(item, key)
                else:
                    walk(nested, str(key))
        elif isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
            for item in obj:
                walk(item, parent_key)
        elif parent_key in _ID_KEYS:
            add(obj)

    walk(value)
    return found


def _derive_tau2_task_rewards(
    *,
    fixture_payload: Mapping[str, Any],
    base_payload: Mapping[str, Any],
    current_step_args: Mapping[str, Any],
) -> tuple[dict[str, float] | None, str | None]:
    explicit_rewards = current_step_args.get("task_rewards") if isinstance(current_step_args, Mapping) else None
    if isinstance(explicit_rewards, Mapping) and explicit_rewards and set(explicit_rewards.keys()) != {"task_1"}:
        return ({str(k): float(v) for k, v in explicit_rewards.items()}, None)

    for source_name, source in (("fixture", fixture_payload), ("base", base_payload)):
        for step in _find_steps_by_name(source, "score_task_bundle"):
            rewards = dict(step.get("args") or {}).get("task_rewards")
            if isinstance(rewards, Mapping) and rewards and set(rewards.keys()) != {"task_1"}:
                return ({str(k): float(v) for k, v in rewards.items()}, f"task_rewards derived from {source_name} score_task_bundle step")

    for source_name, source in (("fixture", fixture_payload), ("base", base_payload)):
        task_ids = _collect_task_ids(source)
        if task_ids:
            return ({task_id: 1.0 for task_id in task_ids}, f"task_rewards derived from {source_name} task ids")

    if isinstance(explicit_rewards, Mapping) and explicit_rewards:
        return ({str(k): float(v) for k, v in explicit_rewards.items()}, None)
    return (None, None)


def _filter_notes_for_seed(notes: Sequence[str], *, seed: int) -> list[str]:
    filtered: list[str] = []
    for note in notes:
        text = str(note).strip()
        if not text:
            continue
        if "Seed 42" in text and int(seed) != 42:
            continue
        filtered.append(text)
    return filtered


def _materialize_scenario_action_plan(
    *,
    domain: str,
    scenario_id: str,
    seed: int,
    action_plan: Sequence[Mapping[str, Any]],
    fixture_payload: Mapping[str, Any],
    base_payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized = _coerce_action_plan(action_plan)
    notes: list[str] = []

    if domain == "business_process" and scenario_id == "saleforceone":
        updated: list[dict[str, Any]] = []
        for step in normalized:
            if step.get("name") != "submit_answer":
                updated.append(step)
                continue
            args = dict(step.get("args") or {})
            answer, note = _derive_business_process_answer(
                seed=seed,
                fixture_payload=fixture_payload,
                base_payload=base_payload,
                current_step_args=args,
            )
            if answer is not None:
                args["answer"] = answer
            updated.append({"name": step["name"], "args": args})
            if note:
                notes.append(note)
        return updated, notes

    if domain == "tau2" and scenario_id == "TicketTwister":
        updated = []
        for step in normalized:
            if step.get("name") != "score_task_bundle":
                updated.append(step)
                continue
            args = dict(step.get("args") or {})
            task_rewards, note = _derive_tau2_task_rewards(
                fixture_payload=fixture_payload,
                base_payload=base_payload,
                current_step_args=args,
            )
            if task_rewards:
                args["task_rewards"] = task_rewards
            updated.append({"name": step["name"], "args": args})
            if note:
                notes.append(note)
        return updated, notes

    return normalized, notes


def _build_canonical_seed_payload(
    base: Mapping[str, Any],
    *,
    domain: str,
    scenario_id: str,
    spec: Mapping[str, Any],
    fixture_payload: Mapping[str, Any] | None,
    fixture_name: str | None,
    level: LevelSpec,
) -> dict[str, Any]:
    payload = deepcopy(dict(base))
    fixture_payload = dict(fixture_payload or {})

    base_reset = dict(payload.get("reset_payload") or {})
    fixture_reset = dict(fixture_payload.get("reset_payload") or {})
    reset_payload = {**base_reset, **fixture_reset}

    base_options = dict(base_reset.get("options") or {})
    fixture_options = dict(fixture_reset.get("options") or {})
    options = {**base_options, **fixture_options}

    original_seed = int(reset_payload.get("seed") or payload.get("seed") or fixture_payload.get("seed") or 42)
    new_seed = original_seed + level.seed_offset

    canonical_scenario_id = _canonicalize_scenario_id(scenario_id)
    mission_id = str(
        fixture_payload.get("mission_id")
        or reset_payload.get("mission_id")
        or spec.get("mission_id")
        or f"{_slugify(canonical_scenario_id)}_{_slugify(domain)}_sample"
    )
    base_mission_stub = str(spec.get("mission_id") or mission_id)
    if mission_id == base_mission_stub or mission_id.endswith("_sample"):
        mission_id = f"{_slugify(canonical_scenario_id)}_{_slugify(domain)}_{level.name}_seed{new_seed}"
    else:
        mission_id = f"{mission_id}_{level.name}_seed{new_seed}"

    max_steps = int(spec.get("max_steps") or options.get("max_steps") or 5)
    target_score = spec.get("target_score")
    if target_score is None:
        target_score = options.get("target_score") if options.get("target_score") is not None else 1

    options["env_id"] = str(spec.get("env_id") or options.get("env_id") or "")
    options["max_steps"] = max(1, max_steps + level.max_steps_delta)
    options["target_score"] = target_score
    options["domain"] = domain
    options["difficulty"] = level.name
    options["curriculum_level"] = level.name
    options["seed_variant"] = new_seed

    reset_payload["seed"] = new_seed
    reset_payload["scenario_id"] = canonical_scenario_id
    reset_payload["mission_id"] = mission_id
    reset_payload["options"] = options

    action_plan = _coerce_action_plan(fixture_payload.get("action_plan"))
    if not action_plan:
        action_plan = _coerce_action_plan(spec.get("default_action_plan"))
    if not action_plan:
        action_plan = _coerce_action_plan(payload.get("action_plan"))
    if not action_plan:
        raise CurriculumBuildError(f"could not resolve canonical action plan for {domain}/{canonical_scenario_id}")

    action_plan, scenario_notes = _materialize_scenario_action_plan(
        domain=domain,
        scenario_id=canonical_scenario_id,
        seed=new_seed,
        action_plan=action_plan,
        fixture_payload=fixture_payload,
        base_payload=payload,
    )

    notes = _dedupe_notes(
        _filter_notes_for_seed(payload.get("notes") or [], seed=new_seed),
        _filter_notes_for_seed(fixture_payload.get("notes") or [], seed=new_seed),
        scenario_notes,
        [f"Realigned curriculum payload for {domain}/{canonical_scenario_id} using canonical fixture/fallback action plan."],
    )

    payload["domain"] = domain
    payload["scenario_id"] = canonical_scenario_id
    payload["seed"] = new_seed
    payload["reset_payload"] = reset_payload
    payload["mission_id"] = mission_id
    payload["action_plan"] = action_plan
    payload["curriculum_level"] = level.name
    payload["curriculum_rank"] = [spec_item.name for spec_item in DEFAULT_LEVELS].index(level.name)
    payload["curriculum_note"] = level.note
    payload["variant_key"] = f"{_slugify(domain)}__{_slugify(canonical_scenario_id)}__{level.name}"
    payload["curriculum_realigned"] = True
    payload["canonical_env_id"] = str(spec.get("env_id") or "")
    payload["canonical_fixture_candidates"] = list(spec.get("fixture_candidates") or [])
    payload["notes"] = notes

    if fixture_name:
        payload["fixture"] = fixture_name
    elif spec.get("fixture_candidates"):
        payload["fixture"] = str(spec.get("fixture_candidates")[0])

    return payload


def build_curriculum_payloads(
    *,
    input_dir: Path,
    output_dir: Path,
    levels: Sequence[str] | None = None,
    only: Sequence[str] | None = None,
) -> dict[str, Any]:
    requested_levels = {name.strip() for name in (levels or []) if name.strip()}
    chosen_levels = [spec for spec in DEFAULT_LEVELS if not requested_levels or spec.name in requested_levels]
    if not chosen_levels:
        raise CurriculumBuildError("no curriculum levels matched the requested filter")

    client_payloads, eval_payloads = _load_aggregate_payloads(input_dir)
    client_index = _make_index(client_payloads)
    eval_index = _make_index(eval_payloads)
    all_keys = sorted(set(client_index) | set(eval_index))
    only_set = _normalize_only(only)

    output_dir.mkdir(parents=True, exist_ok=True)
    curriculum_clients: list[dict[str, Any]] = []
    curriculum_evals: list[dict[str, Any]] = []
    written_files: list[str] = []
    realigned_count = 0

    for domain, scenario_id in all_keys:
        if only_set and domain not in only_set and scenario_id not in only_set:
            continue

        base_client = client_index.get((domain, scenario_id), {})
        base_eval = eval_index.get((domain, scenario_id), {})
        spec = _resolve_spec(domain, scenario_id)
        if spec is None:
            spec = {
                "fixture_candidates": [str(base_client.get("fixture") or base_eval.get("fixture") or "")],
                "env_id": str(
                    (base_client.get("reset_payload") or {}).get("options", {}).get("env_id")
                    or (base_eval.get("reset_payload") or {}).get("options", {}).get("env_id")
                    or ""
                ),
                "mission_id": str(
                    base_client.get("mission_id")
                    or base_eval.get("mission_id")
                    or f"{_slugify(scenario_id)}_{_slugify(domain)}_sample"
                ),
                "max_steps": int(
                    (base_client.get("reset_payload") or {}).get("options", {}).get("max_steps")
                    or (base_eval.get("reset_payload") or {}).get("options", {}).get("max_steps")
                    or 5
                ),
                "target_score": (
                    (base_client.get("reset_payload") or {}).get("options", {}).get("target_score")
                    or (base_eval.get("reset_payload") or {}).get("options", {}).get("target_score")
                    or 1
                ),
                "default_action_plan": _coerce_action_plan(base_client.get("action_plan") or base_eval.get("action_plan")),
            }

        fixture_payload, fixture_name = _load_fixture_override(spec, input_dir)

        for level in chosen_levels:
            client_variant = _build_canonical_seed_payload(
                base_client,
                domain=domain,
                scenario_id=scenario_id,
                spec=spec,
                fixture_payload=fixture_payload,
                fixture_name=fixture_name,
                level=level,
            )
            eval_variant = _build_canonical_seed_payload(
                base_eval or base_client,
                domain=domain,
                scenario_id=scenario_id,
                spec=spec,
                fixture_payload=fixture_payload,
                fixture_name=fixture_name,
                level=level,
            )
            realigned_count += 2

            stem = f"{_slugify(domain)}__{_slugify(scenario_id)}__{level.name}"
            client_name = f"{stem}.client_bundle.json"
            eval_name = f"{stem}.openenv_eval.json"
            _dump_json(output_dir / client_name, client_variant)
            _dump_json(output_dir / eval_name, eval_variant)
            curriculum_clients.append(client_variant)
            curriculum_evals.append(eval_variant)
            written_files.extend([client_name, eval_name])

    if not curriculum_clients:
        raise CurriculumBuildError("no payloads matched the requested filters")

    all_clients_name = "all_client_curriculum_bundles.json"
    all_evals_name = "all_openenv_curriculum_payloads.json"
    index_name = "curriculum_index.json"
    summary = {
        "ok": True,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "count": len(curriculum_clients),
        "levels": [level.name for level in chosen_levels],
        "realigned_count": realigned_count,
        "files": written_files + [all_clients_name, all_evals_name, index_name],
    }
    _dump_json(output_dir / all_clients_name, curriculum_clients)
    _dump_json(output_dir / all_evals_name, curriculum_evals)
    _dump_json(output_dir / index_name, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build curriculum payload variants from generated OpenEnv payloads.")
    parser.add_argument("--input-dir", help="Directory containing all_client_bundles.json and all_openenv_eval_payloads.json")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory where curriculum payloads will be written")
    parser.add_argument("--levels", nargs="*", help="Optional subset of curriculum levels to generate")
    parser.add_argument("--only", nargs="*", help="Restrict generation to one or more domains or scenario IDs")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = build_curriculum_payloads(
            input_dir=_resolve_input_dir(args.input_dir),
            output_dir=Path(args.output_dir).resolve(),
            levels=args.levels,
            only=args.only,
        )
    except CurriculumBuildError as exc:
        report = {"ok": False, "error": str(exc), "type": "contract_error"}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("[ok] curriculum payloads generated")
        print(f"- input_dir: {report['input_dir']}")
        print(f"- output_dir: {report['output_dir']}")
        print(f"- count: {report['count']}")
        print(f"- realigned_count: {report['realigned_count']}")
        print(f"- levels: {', '.join(report['levels'])}")
        for name in report["files"]:
            print(f"- {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
