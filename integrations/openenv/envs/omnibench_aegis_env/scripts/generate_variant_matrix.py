from __future__ import annotations

"""Generate a competitive run matrix from curriculum payloads for phase-B experiments.

Phase B v2 adds more aggressive multi-agent scheduling:
- builds mini leagues for multi_agent scenarios
- assigns round-robin pools and matchdays
- emits per-match telemetry, rivalry metadata, and league-aware scheduling

The file stays backward-compatible with the previous matrix contract: every row still
contains a single payload execution plan, but multi_agent rows now expand into a richer
league schedule instead of a flat repeat list.
"""

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_ROOT = Path(__file__).resolve().parent
ENV_ROOT = SCRIPT_ROOT.parent
DEFAULT_INPUT_CANDIDATES = [
    ENV_ROOT / "training" / "curriculum_payloads",
    ENV_ROOT / "training" / "generated_payloads",
    SCRIPT_ROOT / "generated_payloads",
]
DEFAULT_OUTPUT_DIR = ENV_ROOT / "training" / "variant_matrix"
TRANSFER_DOMAINS = {"business_process", "computer_use", "finance", "multi_agent", "tau2"}

DOMAIN_FAMILIES: dict[str, str] = {
    "research": "analysis",
    "computer_use": "browser_ops",
    "finance": "numerical_reasoning",
    "multi_agent": "arena_competition",
    "tau2": "interactive_dialogue",
    "game": "embodied_strategy",
    "business_process": "policy_workflow",
}

LEVEL_STAGE: dict[str, str] = {
    "easy": "qualifier",
    "medium": "group_stage",
    "hard": "playoff",
    "heldout_like": "championship",
}

COMPETITION_MODES: dict[str, str] = {
    "research": "analysis_trial",
    "computer_use": "speedrun",
    "finance": "precision_trial",
    "multi_agent": "league_match",
    "tau2": "bundle_match",
    "game": "arena_run",
    "business_process": "policy_duel",
}

LEVEL_ORDER = {"easy": 0, "medium": 1, "hard": 2, "heldout_like": 3}
MULTI_AGENT_SIDES = ("blue", "red")
TAU2_USER_ARCHETYPES = ("cooperative", "ambiguous", "adversarial")
PRESSURE_PROFILES = ("stable", "compressed", "high_variance")
BUSINESS_LANES = ("privacy", "support", "renewals", "compliance")
COMPUTER_SPEED_LANES = ("steady", "quick", "surgical")
FINANCE_RISK_BANDS = ("conservative", "balanced", "stress")
GAME_ARENAS = ("tactical", "attrition", "speedclear")
RIVALRY_TIERS = ("routine", "hot", "heated", "marquee")


class VariantMatrixError(RuntimeError):
    pass


def _slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return value or "item"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _resolve_input_dir(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).resolve()
        if not path.exists():
            raise VariantMatrixError(f"input directory does not exist: {path}")
        return path
    for candidate in DEFAULT_INPUT_CANDIDATES:
        if candidate.exists():
            return candidate
    tried = ", ".join(str(p) for p in DEFAULT_INPUT_CANDIDATES)
    raise VariantMatrixError(f"could not locate payload directory; tried: {tried}")


def _normalize_only(values: Sequence[str] | None) -> set[str]:
    return {str(v).strip() for v in (values or []) if str(v).strip()}


def _load_payload_list(input_dir: Path) -> list[dict[str, Any]]:
    candidates = [
        input_dir / "all_openenv_curriculum_payloads.json",
        input_dir / "all_openenv_eval_payloads.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            payload = _load_json(candidate)
            if isinstance(payload, list):
                return [dict(item) for item in payload if isinstance(item, Mapping)]
            raise VariantMatrixError(f"payload file must contain a JSON list: {candidate}")
    raise VariantMatrixError("could not find a supported aggregate payload file in the input directory")


def _stable_bucket(*parts: Any, modulo: int) -> int:
    raw = "::".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % modulo


def _stable_token(*parts: Any, length: int = 12) -> str:
    raw = "::".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _infer_split(input_dir: Path, output_dir: Path, payloads: Sequence[Mapping[str, Any]]) -> str:
    joined = f"{input_dir} {output_dir}".lower()
    if "transfer" in joined:
        return "transfer"
    levels = {
        str(p.get("curriculum_level") or (p.get("reset_payload") or {}).get("options", {}).get("difficulty") or "")
        for p in payloads
    }
    domains = {str(p.get("domain") or "") for p in payloads}
    if levels == {"heldout_like"} and domains and domains.issubset(TRANSFER_DOMAINS):
        return "transfer"
    return "curriculum"


def _infer_family(domain: str, scenario_id: str) -> str:
    family = DOMAIN_FAMILIES.get(domain)
    if family:
        return family
    slug = _slugify(scenario_id)
    if "ticket" in slug or "dialog" in slug:
        return "interactive_dialogue"
    if "agent" in slug or "bot" in slug:
        return "arena_competition"
    return "general"


def _build_telemetry_tags(
    *,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    family: str,
    repeat_index: int,
    seed: int,
    payload: Mapping[str, Any],
) -> list[str]:
    tags = [
        f"split:{split}",
        f"domain:{domain}",
        f"scenario:{_slugify(scenario_id)}",
        f"level:{level}",
        f"family:{family}",
        f"repeat:r{repeat_index:02d}",
        f"seed:{seed}",
    ]
    fixture = str(payload.get("fixture") or "").strip()
    if fixture:
        tags.append(f"fixture:{_slugify(fixture)}")
    env_id = str((payload.get("reset_payload") or {}).get("options", {}).get("env_id") or payload.get("canonical_env_id") or "").strip()
    if env_id:
        tags.append(f"env:{_slugify(env_id)}")
    if bool(payload.get("curriculum_realigned")):
        tags.append("realigned:true")
    mode = COMPETITION_MODES.get(domain, "trial")
    tags.append(f"mode:{mode}")
    return tags


def _build_competitive_context(
    *,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    family: str,
    seed: int,
    repeat_index: int,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    stage = LEVEL_STAGE.get(level, "open")
    mode = COMPETITION_MODES.get(domain, "trial")
    context: dict[str, Any] = {
        "mode": mode,
        "stage": stage,
        "family": family,
        "split": split,
        "heat": _stable_bucket(domain, scenario_id, level, repeat_index, modulo=4) + 1,
        "series": f"{_slugify(domain)}__{_slugify(scenario_id)}__{_slugify(level)}",
        "pressure_profile": PRESSURE_PROFILES[_stable_bucket(domain, scenario_id, repeat_index, modulo=len(PRESSURE_PROFILES))],
    }

    if domain == "multi_agent":
        division = chr(ord("A") + _stable_bucket(scenario_id, level, modulo=4))
        seat_index = _stable_bucket(seed, repeat_index, scenario_id, modulo=len(MULTI_AGENT_SIDES))
        context.update(
            {
                "division": f"arena-{division}",
                "round": _stable_bucket(level, repeat_index, seed, modulo=6) + 1,
                "side": MULTI_AGENT_SIDES[seat_index],
                "opponent_profile": f"opponent-{_stable_bucket(scenario_id, seed, repeat_index, modulo=8) + 1:02d}",
                "ladder_points_on_entry": 90 + _stable_bucket(seed, scenario_id, modulo=31),
                "stakes": "head_to_head_equilibrium",
            }
        )
    elif domain == "tau2":
        context.update(
            {
                "user_archetype": TAU2_USER_ARCHETYPES[_stable_bucket(scenario_id, seed, repeat_index, modulo=len(TAU2_USER_ARCHETYPES))],
                "bundle_lane": f"lane-{_stable_bucket(seed, level, repeat_index, modulo=5) + 1}",
                "dialogue_pressure": PRESSURE_PROFILES[_stable_bucket("tau2", scenario_id, repeat_index, modulo=len(PRESSURE_PROFILES))],
                "stakes": "bundle_score_margin",
            }
        )
    elif domain == "business_process":
        context.update(
            {
                "workflow_lane": BUSINESS_LANES[_stable_bucket(scenario_id, seed, repeat_index, modulo=len(BUSINESS_LANES))],
                "policy_surface": "privacy_and_routing",
                "stakes": "policy_correctness",
            }
        )
    elif domain == "computer_use":
        context.update(
            {
                "execution_lane": COMPUTER_SPEED_LANES[_stable_bucket(seed, repeat_index, modulo=len(COMPUTER_SPEED_LANES))],
                "stakes": "navigation_speed_and_accuracy",
            }
        )
    elif domain == "finance":
        context.update(
            {
                "risk_band": FINANCE_RISK_BANDS[_stable_bucket(scenario_id, seed, modulo=len(FINANCE_RISK_BANDS))],
                "stakes": "numeric_precision",
            }
        )
    elif domain == "game":
        context.update(
            {
                "arena_type": GAME_ARENAS[_stable_bucket(scenario_id, seed, repeat_index, modulo=len(GAME_ARENAS))],
                "stakes": "mission_completion",
            }
        )
    else:
        context.update({"stakes": "benchmark_score"})

    mission_id = str(payload.get("mission_id") or (payload.get("reset_payload") or {}).get("mission_id") or "").strip()
    if mission_id:
        context["mission_id"] = mission_id
    return context


def _build_telemetry(
    *,
    run_id: str,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    family: str,
    repeat_index: int,
    seed: int,
    payload: Mapping[str, Any],
    competitive_context: Mapping[str, Any],
) -> dict[str, Any]:
    trace_namespace = f"phaseb.{split}.{_slugify(domain)}.{_slugify(scenario_id)}"
    return {
        "trace_namespace": trace_namespace,
        "trace_id": _stable_token(run_id, seed, repeat_index, length=16),
        "span_group": f"{_slugify(domain)}__{_slugify(level)}__{competitive_context.get('stage', 'open')}",
        "experiment_group": f"{split}__{_slugify(domain)}__{_slugify(scenario_id)}__{_slugify(level)}",
        "benchmark_track": family,
        "tags": _build_telemetry_tags(
            domain=domain,
            scenario_id=scenario_id,
            level=level,
            split=split,
            family=family,
            repeat_index=repeat_index,
            seed=seed,
            payload=payload,
        ),
        "lineage": {
            "split": split,
            "domain": domain,
            "scenario_id": scenario_id,
            "curriculum_level": level,
            "repeat_index": repeat_index,
            "seed": seed,
            "fixture": payload.get("fixture"),
            "variant_key": payload.get("variant_key"),
        },
    }


def _build_scheduling(
    *,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    repeat_index: int,
    seed: int,
    competitive_context: Mapping[str, Any],
) -> dict[str, Any]:
    stage = str(competitive_context.get("stage") or LEVEL_STAGE.get(level, "open"))
    domain_rank = {
        "multi_agent": 0,
        "tau2": 1,
        "business_process": 2,
        "computer_use": 3,
        "finance": 4,
        "game": 5,
        "research": 6,
    }.get(domain, 7)
    stage_rank = {"qualifier": 0, "group_stage": 1, "playoff": 2, "championship": 3, "league_stage": 4}.get(stage, 9)
    urgency = _stable_bucket(domain, scenario_id, level, split, seed, modulo=100)
    return {
        "queue_tier": stage,
        "domain_rank": domain_rank,
        "stage_rank": stage_rank,
        "priority_score": (domain_rank * 1000) + (stage_rank * 100) + urgency,
        "batch_key": f"{split}__{_slugify(domain)}__{stage}",
        "shard": _stable_bucket(domain, scenario_id, repeat_index, modulo=4) + 1,
    }


def _build_base_row(
    *,
    index: int,
    payload: Mapping[str, Any],
    split: str,
    repeat_index: int,
) -> dict[str, Any]:
    domain = str(payload.get("domain") or "general")
    scenario_id = str(payload.get("scenario_id") or "UnknownScenario")
    level = str(payload.get("curriculum_level") or payload.get("reset_payload", {}).get("options", {}).get("difficulty") or "baseline")
    reset_payload = dict(payload.get("reset_payload") or {})
    seed = int(reset_payload.get("seed") or payload.get("seed") or 42)
    family = _infer_family(domain, scenario_id)
    run_id = f"{index:03d}__{_slugify(domain)}__{_slugify(scenario_id)}__{_slugify(level)}__r{repeat_index:02d}"
    competitive_context = _build_competitive_context(
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        family=family,
        seed=seed,
        repeat_index=repeat_index,
        payload=payload,
    )
    telemetry = _build_telemetry(
        run_id=run_id,
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        family=family,
        repeat_index=repeat_index,
        seed=seed,
        payload=payload,
        competitive_context=competitive_context,
    )
    scheduling = _build_scheduling(
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        repeat_index=repeat_index,
        seed=seed,
        competitive_context=competitive_context,
    )
    return {
        "run_id": run_id,
        "domain": domain,
        "scenario_id": scenario_id,
        "curriculum_level": level,
        "split": split,
        "family": family,
        "seed": seed,
        "repeat_index": repeat_index,
        "base_url": payload.get("base_url") or payload.get("environment_url"),
        "experiment_group": telemetry["experiment_group"],
        "competitive_context": competitive_context,
        "telemetry": telemetry,
        "scheduling": scheduling,
        "payload": dict(payload),
    }


def _snake_partition(participants: list[dict[str, Any]], pool_size: int) -> list[list[dict[str, Any]]]:
    if not participants:
        return []
    pool_count = max(1, math.ceil(len(participants) / max(2, pool_size)))
    pools: list[list[dict[str, Any]]] = [[] for _ in range(pool_count)]
    direction = 1
    pool_index = 0
    for participant in participants:
        pools[pool_index].append(participant)
        if pool_count == 1:
            continue
        next_index = pool_index + direction
        if next_index >= pool_count:
            direction = -1
            pool_index = pool_count - 1
        elif next_index < 0:
            direction = 1
            pool_index = 0
        else:
            pool_index = next_index
    return [pool for pool in pools if pool]


def _round_robin_pairs(participants: list[dict[str, Any]]) -> list[list[tuple[dict[str, Any], dict[str, Any]]]]:
    if len(participants) < 2:
        return []
    slots: list[dict[str, Any] | None] = list(participants)
    if len(slots) % 2 == 1:
        slots.append(None)
    rounds: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
    total_rounds = len(slots) - 1
    for round_index in range(total_rounds):
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        half = len(slots) // 2
        for i in range(half):
            left = slots[i]
            right = slots[-(i + 1)]
            if left is None or right is None:
                continue
            if round_index % 2 == 0:
                home, away = left, right
            else:
                home, away = right, left
            pairs.append((home, away))
        rounds.append(pairs)
        fixed = slots[0]
        rest = slots[1:]
        rest = [rest[-1], *rest[:-1]]
        slots = [fixed, *rest]
    return rounds


def _build_league_row(
    *,
    participant: Mapping[str, Any],
    opponent: Mapping[str, Any],
    league_id: str,
    pool_id: str,
    round_no: int,
    fixture_no: int,
    leg_no: int,
    seat: str,
    home_away: str,
    pool_size: int,
    pool_slot: int,
    group_key: str,
) -> dict[str, Any]:
    row = deepcopy(dict(participant))
    opponent_run_id = str(opponent["run_id"])
    new_run_id = (
        f"{participant['run_id']}__rr__{_slugify(pool_id)}__rd{round_no:02d}"
        f"__fx{fixture_no:02d}__leg{leg_no:02d}__vs__{_slugify(opponent_run_id)}"
    )
    rivalry_score = _stable_bucket(participant["run_id"], opponent_run_id, round_no, leg_no, modulo=100)
    rivalry_tier = RIVALRY_TIERS[min(len(RIVALRY_TIERS) - 1, rivalry_score // 25)]

    row["run_id"] = new_run_id
    row["experiment_group"] = f"{participant['experiment_group']}__round_robin__{_slugify(pool_id)}"

    cc = dict(row.get("competitive_context") or {})
    cc.update(
        {
            "mode": "round_robin_league",
            "stage": "league_stage",
            "league_id": league_id,
            "pool_id": pool_id,
            "group_key": group_key,
            "pool_size": pool_size,
            "pool_slot": pool_slot,
            "round_no": round_no,
            "fixture_no": fixture_no,
            "leg_no": leg_no,
            "seat": seat,
            "home_away": home_away,
            "opponent_run_id": opponent_run_id,
            "opponent_seed": opponent["seed"],
            "opponent_level": opponent["curriculum_level"],
            "opponent_repeat_index": opponent["repeat_index"],
            "opponent_profile": f"league_opp_{_stable_token(opponent_run_id, length=8)}",
            "rivalry_tier": rivalry_tier,
            "rivalry_score": rivalry_score,
            "table_points_on_entry": 6 + _stable_bucket(participant["seed"], opponent["seed"], modulo=10),
            "schedule_kind": "mini_league_round_robin",
        }
    )
    row["competitive_context"] = cc

    telemetry = deepcopy(dict(row.get("telemetry") or {}))
    telemetry["trace_id"] = _stable_token(new_run_id, participant["seed"], round_no, fixture_no, leg_no, length=16)
    telemetry["span_group"] = f"multi_agent__round_robin__rd{round_no:02d}"
    telemetry["experiment_group"] = row["experiment_group"]
    tags = list(telemetry.get("tags") or [])
    tags.extend(
        [
            "mode:round_robin_league",
            f"league:{_slugify(league_id)}",
            f"pool:{_slugify(pool_id)}",
            f"round:{round_no}",
            f"fixture:{fixture_no}",
            f"leg:{leg_no}",
            f"seat:{seat}",
            f"opponent_level:{opponent['curriculum_level']}",
            f"home_away:{home_away}",
            f"rivalry:{rivalry_tier}",
        ]
    )
    telemetry["tags"] = list(dict.fromkeys(tags))
    lineage = dict(telemetry.get("lineage") or {})
    lineage.update(
        {
            "league_id": league_id,
            "pool_id": pool_id,
            "round_no": round_no,
            "fixture_no": fixture_no,
            "leg_no": leg_no,
            "seat": seat,
            "opponent_run_id": opponent_run_id,
            "group_key": group_key,
        }
    )
    telemetry["lineage"] = lineage
    row["telemetry"] = telemetry

    scheduling = deepcopy(dict(row.get("scheduling") or {}))
    scheduling.update(
        {
            "queue_tier": "league_stage",
            "stage_rank": 4,
            "batch_key": f"{participant['split']}__multi_agent__league__rd{round_no:02d}",
            "league_id": league_id,
            "pool_id": pool_id,
            "round_no": round_no,
            "fixture_no": fixture_no,
            "leg_no": leg_no,
            "match_shard": _stable_bucket(league_id, pool_id, round_no, fixture_no, modulo=4) + 1,
        }
    )
    scheduling["priority_score"] = min(scheduling.get("priority_score", 999999), 250 + round_no * 10 + fixture_no)
    row["scheduling"] = scheduling

    row["league"] = {
        "league_id": league_id,
        "pool_id": pool_id,
        "group_key": group_key,
        "round_no": round_no,
        "fixture_no": fixture_no,
        "leg_no": leg_no,
        "seat": seat,
        "home_away": home_away,
        "pool_size": pool_size,
        "pool_slot": pool_slot,
        "participant_run_id": participant["run_id"],
        "opponent_run_id": opponent_run_id,
    }
    return row


def _expand_multi_agent_round_robin(
    participants: list[dict[str, Any]],
    *,
    pool_size: int,
    double_round_robin: bool,
    mix_levels: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not participants:
        return [], []

    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in participants:
        key = (row["split"], row["scenario_id"]) if mix_levels else (row["split"], row["scenario_id"], row["curriculum_level"])
        grouped.setdefault(key, []).append(row)

    expanded_rows: list[dict[str, Any]] = []
    league_index: list[dict[str, Any]] = []

    for key, group_rows in sorted(grouped.items()):
        split = key[0]
        scenario_id = key[1]
        key_slug = "__".join(_slugify(part) for part in key)
        ordered = sorted(
            group_rows,
            key=lambda row: (
                LEVEL_ORDER.get(str(row["curriculum_level"]), 99),
                int(row["seed"]),
                int(row["repeat_index"]),
                str(row["run_id"]),
            ),
        )
        pools = _snake_partition(ordered, pool_size)
        for pool_number, pool in enumerate(pools, start=1):
            league_id = f"{split}__{_slugify(scenario_id)}__league_{pool_number:02d}"
            pool_id = f"{league_id}__pool"
            rounds = _round_robin_pairs(pool)
            league_index.append(
                {
                    "league_id": league_id,
                    "pool_id": pool_id,
                    "group_key": key_slug,
                    "scenario_id": scenario_id,
                    "split": split,
                    "pool_size": len(pool),
                    "participants": [row["run_id"] for row in pool],
                    "rounds": len(rounds) * (2 if double_round_robin else 1),
                    "double_round_robin": double_round_robin,
                }
            )
            fixture_counter = 0
            legs = (1, 2) if double_round_robin else (1,)
            for leg_no in legs:
                leg_rounds = rounds if leg_no == 1 else [[(away, home) for home, away in pairings] for pairings in rounds]
                round_offset = (leg_no - 1) * len(rounds)
                for local_round_no, pairings in enumerate(leg_rounds, start=1):
                    round_no = round_offset + local_round_no
                    for match_no, (home, away) in enumerate(pairings, start=1):
                        fixture_counter += 1
                        try:
                            home_slot = pool.index(home) + 1
                            away_slot = pool.index(away) + 1
                        except ValueError:
                            home_slot = 1
                            away_slot = 2
                        expanded_rows.append(
                            _build_league_row(
                                participant=home,
                                opponent=away,
                                league_id=league_id,
                                pool_id=pool_id,
                                round_no=round_no,
                                fixture_no=fixture_counter,
                                leg_no=leg_no,
                                seat="blue",
                                home_away="home",
                                pool_size=len(pool),
                                pool_slot=home_slot,
                                group_key=key_slug,
                            )
                        )
                        expanded_rows.append(
                            _build_league_row(
                                participant=away,
                                opponent=home,
                                league_id=league_id,
                                pool_id=pool_id,
                                round_no=round_no,
                                fixture_no=fixture_counter,
                                leg_no=leg_no,
                                seat="red",
                                home_away="away",
                                pool_size=len(pool),
                                pool_slot=away_slot,
                                group_key=key_slug,
                            )
                        )
    return expanded_rows, league_index


def generate_variant_matrix(
    *,
    input_dir: Path,
    output_dir: Path,
    repeats: int,
    only: Sequence[str] | None = None,
    multi_agent_pool_size: int = 4,
    multi_agent_double_round_robin: bool = False,
    multi_agent_mix_levels: bool = True,
) -> dict[str, Any]:
    if repeats < 1:
        raise VariantMatrixError("repeats must be >= 1")
    if multi_agent_pool_size < 2:
        raise VariantMatrixError("multi_agent_pool_size must be >= 2")

    payloads = _load_payload_list(input_dir)
    only_set = _normalize_only(only)
    split = _infer_split(input_dir, output_dir, payloads)

    matrix: list[dict[str, Any]] = []
    multi_agent_participants: list[dict[str, Any]] = []

    for index, payload in enumerate(payloads, start=1):
        domain = str(payload.get("domain") or "general")
        scenario_id = str(payload.get("scenario_id") or "UnknownScenario")
        level = str(payload.get("curriculum_level") or payload.get("reset_payload", {}).get("options", {}).get("difficulty") or "baseline")
        if only_set and domain not in only_set and scenario_id not in only_set and level not in only_set:
            continue
        for repeat_index in range(1, repeats + 1):
            row = _build_base_row(index=index, payload=payload, split=split, repeat_index=repeat_index)
            if domain == "multi_agent":
                multi_agent_participants.append(row)
            else:
                matrix.append(row)

    league_index: list[dict[str, Any]] = []
    league_rows: list[dict[str, Any]] = []
    if multi_agent_participants:
        league_rows, league_index = _expand_multi_agent_round_robin(
            multi_agent_participants,
            pool_size=multi_agent_pool_size,
            double_round_robin=multi_agent_double_round_robin,
            mix_levels=multi_agent_mix_levels,
        )
        matrix.extend(league_rows)

    if not matrix:
        raise VariantMatrixError("no payloads matched the requested filters")

    domain_counter: Counter[str] = Counter()
    level_counter: Counter[str] = Counter()
    family_counter: Counter[str] = Counter()
    mode_counter: Counter[str] = Counter()
    stage_counter: Counter[str] = Counter()
    league_counter: Counter[str] = Counter()

    matrix.sort(key=lambda row: (row["scheduling"]["priority_score"], row["run_id"]))
    for order_index, row in enumerate(matrix, start=1):
        row["schedule_index"] = order_index
        domain_counter[str(row["domain"])] += 1
        level_counter[str(row["curriculum_level"])] += 1
        family_counter[str(row["family"])] += 1
        mode_counter[str((row.get("competitive_context") or {}).get("mode") or "trial")] += 1
        stage_counter[str((row.get("competitive_context") or {}).get("stage") or "open")] += 1
        if "league" in row:
            league_counter[str(row["league"]["league_id"])] += 1

    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_name = "variant_matrix.json"
    summary_name = "variant_matrix_summary.json"
    league_name = "league_index.json"
    summary = {
        "ok": True,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "count": len(matrix),
        "repeats": repeats,
        "split": split,
        "domains": sorted(domain_counter),
        "levels": sorted(level_counter),
        "families": sorted(family_counter),
        "competition_modes": dict(sorted(mode_counter.items())),
        "stages": dict(sorted(stage_counter.items())),
        "rows_by_domain": dict(sorted(domain_counter.items())),
        "rows_by_level": dict(sorted(level_counter.items())),
        "rows_by_family": dict(sorted(family_counter.items())),
        "telemetry_enabled": True,
        "competitive_metadata_enabled": True,
        "multi_agent_round_robin_enabled": bool(league_index),
        "multi_agent_pool_size": multi_agent_pool_size,
        "multi_agent_double_round_robin": multi_agent_double_round_robin,
        "multi_agent_mix_levels": multi_agent_mix_levels,
        "league_count": len(league_index),
        "league_rows": sum(league_counter.values()),
        "league_row_distribution": dict(sorted(league_counter.items())),
        "files": [matrix_name, summary_name, league_name],
    }
    _dump_json(output_dir / matrix_name, matrix)
    _dump_json(output_dir / summary_name, summary)
    _dump_json(output_dir / league_name, league_index)
    return {
        "ok": True,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "count": len(matrix),
        "repeats": repeats,
        "split": split,
        "files": [matrix_name, summary_name, league_name],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a competitive run matrix from curriculum or generated payloads.")
    parser.add_argument("--input-dir", help="Directory containing aggregate curriculum or OpenEnv eval payloads")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory where the variant matrix will be written")
    parser.add_argument("--repeats", type=int, default=1, help="How many repeats to schedule per payload entry")
    parser.add_argument("--only", nargs="*", help="Restrict to one or more domains, scenario IDs, or levels")
    parser.add_argument("--multi-agent-pool-size", type=int, default=4, help="Pool size for multi_agent mini leagues")
    parser.add_argument("--multi-agent-double-round-robin", action="store_true", help="Schedule home-and-away legs for each mini league")
    parser.add_argument("--no-multi-agent-mix-levels", action="store_true", help="Keep each curriculum level in separate mini leagues")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = generate_variant_matrix(
            input_dir=_resolve_input_dir(args.input_dir),
            output_dir=Path(args.output_dir).resolve(),
            repeats=args.repeats,
            only=args.only,
            multi_agent_pool_size=args.multi_agent_pool_size,
            multi_agent_double_round_robin=args.multi_agent_double_round_robin,
            multi_agent_mix_levels=not args.no_multi_agent_mix_levels,
        )
    except VariantMatrixError as exc:
        report = {"ok": False, "error": str(exc), "type": "contract_error"}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("[ok] competitive variant matrix generated")
        print(f"- input_dir: {report['input_dir']}")
        print(f"- output_dir: {report['output_dir']}")
        print(f"- count: {report['count']}")
        print(f"- repeats: {report['repeats']}")
        print(f"- split: {report['split']}")
        for name in report["files"]:
            print(f"- {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
