from __future__ import annotations

"""Web domain for omnibench_aegis_env.

Primary scenario:
- comtrade_web_retrieval

Benchmark-safe version:
- deterministic offline API-retrieval style task
- pagination, retry/backoff, deduplication, page drift, totals-trap handling
- file-contract awareness without touching real external services
- traceability and cost discipline inspired by web/API-agent evaluation loops
"""

from copy import deepcopy
from random import Random
from typing import Any

from ..base import BaseDomain, StepOutcome


WEB_ENV_ID = "omnibench_aegis_env:web.comtrade_retrieval"
WEB_SCENARIO_ID = "comtrade_web_retrieval"


_DEDUP_KEY = ["year", "reporter", "partner", "flow", "hs", "record_id"]
_REQUIRED_OUTPUT_FILES = ["data.jsonl", "metadata.json", "run.log"]


_VARIANTS: list[dict[str, Any]] = [
    {
        "variant_id": "T1_single_page",
        "task_id": "T1_single_page",
        "task_label": "Single Page Retrieval",
        "fault_mode": "none",
        "expected_pages": 1,
        "expected_rows": 120,
        "expected_requests": 1,
        "query": {"reporter": "840", "partner": "156", "flow": "M", "hs": "85", "year": 2021},
        "has_duplicates": False,
        "has_page_drift": False,
        "has_totals_rows": False,
        "requires_retry": False,
        "retry_signal": "",
        "special_requirement": "fetch the only page and write the three required output files",
    },
    {
        "variant_id": "T2_multi_page",
        "task_id": "T2_multi_page",
        "task_label": "Multi-page Retrieval",
        "fault_mode": "pagination",
        "expected_pages": 5,
        "expected_rows": 620,
        "expected_requests": 5,
        "query": {"reporter": "392", "partner": "840", "flow": "X", "hs": "87", "year": 2022},
        "has_duplicates": False,
        "has_page_drift": False,
        "has_totals_rows": False,
        "requires_retry": False,
        "retry_signal": "",
        "special_requirement": "fetch all pages until completion, not just page one",
    },
    {
        "variant_id": "T3_duplicates",
        "task_id": "T3_duplicates",
        "task_label": "Duplicate Records",
        "fault_mode": "duplicates",
        "expected_pages": 3,
        "expected_rows": 420,
        "expected_requests": 3,
        "query": {"reporter": "276", "partner": "156", "flow": "M", "hs": "90", "year": 2020},
        "has_duplicates": True,
        "has_page_drift": False,
        "has_totals_rows": False,
        "requires_retry": False,
        "retry_signal": "",
        "special_requirement": "deduplicate records using the declared primary key",
    },
    {
        "variant_id": "T4_rate_limit_429",
        "task_id": "T4_rate_limit_429",
        "task_label": "Rate Limit 429",
        "fault_mode": "rate_limit_429",
        "expected_pages": 4,
        "expected_rows": 510,
        "expected_requests": 4,
        "query": {"reporter": "484", "partner": "840", "flow": "X", "hs": "07", "year": 2021},
        "has_duplicates": False,
        "has_page_drift": False,
        "has_totals_rows": False,
        "requires_retry": True,
        "retry_signal": "429",
        "special_requirement": "log 429 handling and retry/backoff evidence",
    },
    {
        "variant_id": "T5_server_error_500",
        "task_id": "T5_server_error_500",
        "task_label": "Server Error 500",
        "fault_mode": "server_error_500",
        "expected_pages": 4,
        "expected_rows": 500,
        "expected_requests": 4,
        "query": {"reporter": "124", "partner": "840", "flow": "M", "hs": "44", "year": 2023},
        "has_duplicates": False,
        "has_page_drift": False,
        "has_totals_rows": False,
        "requires_retry": True,
        "retry_signal": "500",
        "special_requirement": "log 500 handling and retry evidence",
    },
    {
        "variant_id": "T6_page_drift",
        "task_id": "T6_page_drift",
        "task_label": "Page Drift",
        "fault_mode": "page_drift",
        "expected_pages": 3,
        "expected_rows": 360,
        "expected_requests": 3,
        "query": {"reporter": "410", "partner": "156", "flow": "X", "hs": "39", "year": 2022},
        "has_duplicates": True,
        "has_page_drift": True,
        "has_totals_rows": False,
        "requires_retry": False,
        "retry_signal": "",
        "special_requirement": "use canonical sorting and key-based deduplication under drift",
    },
    {
        "variant_id": "T7_totals_trap",
        "task_id": "T7_totals_trap",
        "task_label": "Totals Trap",
        "fault_mode": "totals_trap",
        "expected_pages": 8,
        "expected_rows": 940,
        "expected_requests": 8,
        "query": {"reporter": "840", "partner": "251", "flow": "M", "hs": "TOTAL", "year": 2021},
        "has_duplicates": False,
        "has_page_drift": False,
        "has_totals_rows": True,
        "requires_retry": False,
        "retry_signal": "",
        "special_requirement": "drop totals marker rows and report totals_handling metadata",
    },
]


class WebComtradeRetrievalEnv(BaseDomain):
    domain_name = "web"
    env_name = "omnibench_aegis_env"
    default_max_steps = 9
    default_target_progress = 100

    def list_task_categories(self) -> list[str]:
        return [
            "web",
            "api_retrieval",
            "pagination",
            "fault_tolerance",
            "data_quality",
            "observability",
        ]

    def normalize_action(self, action: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(action or {})
        if "name" in raw:
            return {
                "name": str(raw.get("name") or "").strip(),
                "args": dict(raw.get("args") or {}),
            }
        if "action" in raw:
            args = dict(raw.get("args") or {})
            for key, value in raw.items():
                if key not in {"action", "args"}:
                    args[key] = value
            return {
                "name": str(raw.get("action") or "").strip(),
                "args": args,
            }
        return {"name": "", "args": {}}

    def default_action(self) -> dict[str, Any]:
        return {"name": "", "args": {}}

    def scripted_good_path(self) -> list[str]:
        return [
            "inspect_task",
            "configure_query",
            "fetch_records",
            "canonicalize_records",
            "deduplicate_records",
            "drop_totals_rows",
            "validate_metadata",
            "write_outputs",
            "submit_run",
        ]

    def build_initial_state(self, **kwargs: Any) -> dict[str, Any]:
        scenario_id = str(kwargs.get("scenario_id") or WEB_SCENARIO_ID)
        mission = str(
            kwargs.get("mission")
            or "Resolve the web/API retrieval task: inspect the task, configure the expected query, fetch all pages, "
               "handle faults, normalize records, deduplicate, drop totals traps, validate metadata, write the output "
               "contract, and submit one auditable run."
        )
        seed = int(kwargs.get("seed", 0) or 0)
        max_steps = int(kwargs.get("max_steps", self.default_max_steps) or self.default_max_steps)

        rng = Random(seed)
        variant = deepcopy(_VARIANTS[seed % len(_VARIANTS)])

        visible_fault_hints = [
            f"Fault mode hint: {variant['fault_mode'].replace('_', ' ')}.",
            "The mock service is deterministic and offline; do not call external networks.",
            "The output contract requires data.jsonl, metadata.json, and run.log.",
            "Traceability matters: log pages, retry evidence, deduplication, and completion.",
        ]
        if variant["has_totals_rows"]:
            visible_fault_hints.append("Some records may be aggregate totals markers rather than data rows.")
        if variant["has_page_drift"]:
            visible_fault_hints.append("Page boundaries may drift; stable keys matter more than row positions.")
        if variant["requires_retry"]:
            visible_fault_hints.append(f"Transient HTTP {variant['retry_signal']} events require retry evidence.")
        rng.shuffle(visible_fault_hints)

        candidate_tasks = [item["task_id"] for item in _VARIANTS]
        rng.shuffle(candidate_tasks)

        public_task_brief = {
            "task_id": variant["task_id"],
            "task_label": variant["task_label"],
            "mock_api_style": "comtrade-like_records",
            "query": deepcopy(variant["query"]),
            "expected_output_files": list(_REQUIRED_OUTPUT_FILES),
            "dedup_key": list(_DEDUP_KEY),
            "candidate_tasks": candidate_tasks[:4],
            "special_requirement": variant["special_requirement"],
        }

        return {
            "domain": self.domain_name,
            "seed": seed,
            "scenario_id": scenario_id,
            "mission": mission,
            "task_category": "api_retrieval",
            "max_steps": max_steps,
            "progress": 0,
            "target_progress": self.default_target_progress,
            "score": 0.0,
            "done": False,
            "success": False,
            "final_outcome": "in_progress",
            "failure_mode": "none",
            "terminal_reason": "in_progress",
            "compromise_type": "none",
            "collection_score": 0.0,
            "robustness_score": 0.0,
            "data_quality_score": 0.0,
            "observability_score": 0.0,
            "contract_score": 0.0,
            "efficiency_score": 100,
            "task_inspected": False,
            "query_configured": False,
            "records_fetched": False,
            "records_canonicalized": False,
            "records_deduplicated": False,
            "totals_rows_dropped": False,
            "metadata_validated": False,
            "outputs_written": False,
            "run_submitted": False,
            "premature_submission": False,
            "wrong_query": False,
            "incomplete_pagination": False,
            "missing_retry_evidence": False,
            "duplicate_rows_present": False,
            "totals_rows_written": False,
            "unstable_order": False,
            "missing_required_file": False,
            "external_network_attempted": False,
            "pages_fetched": 0,
            "records_raw": 0,
            "records_clean": 0,
            "requests_total": 0,
            "retries_total": 0,
            "http_429": 0,
            "http_500": 0,
            "written_files": [],
            "final_answer": None,
            "public_task_brief": public_task_brief,
            "visible_notes": visible_fault_hints,
            "event_log": [],
            "hidden_variant_id": variant["variant_id"],
            "hidden_task_id": variant["task_id"],
            "hidden_fault_mode": variant["fault_mode"],
            "hidden_expected_pages": int(variant["expected_pages"]),
            "hidden_expected_rows": int(variant["expected_rows"]),
            "hidden_expected_requests": int(variant["expected_requests"]),
            "hidden_query": deepcopy(variant["query"]),
            "hidden_has_duplicates": bool(variant["has_duplicates"]),
            "hidden_has_page_drift": bool(variant["has_page_drift"]),
            "hidden_has_totals_rows": bool(variant["has_totals_rows"]),
            "hidden_requires_retry": bool(variant["requires_retry"]),
            "hidden_retry_signal": str(variant["retry_signal"]),
            "hidden_dedup_key": list(_DEDUP_KEY),
            "metadata": {
                "env_id": WEB_ENV_ID,
                "scenario_id": scenario_id,
            },
        }

    def get_observation(self, state: dict[str, Any]) -> dict[str, Any]:
        alerts: list[str] = []
        if state["task_inspected"]:
            alerts.append("The retrieval task was inspected.")
        if state["query_configured"]:
            alerts.append("The API query is configured.")
        if state["wrong_query"]:
            alerts.append("The configured query does not match the task contract.")
        if state["records_fetched"]:
            alerts.append("Records were fetched from the mock service.")
        if state["incomplete_pagination"]:
            alerts.append("Pagination appears incomplete.")
        if state["missing_retry_evidence"]:
            alerts.append("A transient fault was encountered without sufficient retry/backoff evidence.")
        if state["records_canonicalized"]:
            alerts.append("Records were canonicalized into stable order.")
        if state["records_deduplicated"]:
            alerts.append("Deduplication was performed using the declared key.")
        if state["duplicate_rows_present"]:
            alerts.append("Duplicate rows remain in the candidate output.")
        if state["totals_rows_dropped"]:
            alerts.append("Totals-trap handling has been applied.")
        if state["totals_rows_written"]:
            alerts.append("Aggregate totals rows would leak into data.jsonl.")
        if state["metadata_validated"]:
            alerts.append("Metadata contract validation was performed.")
        if state["outputs_written"]:
            alerts.append("Required output files were prepared.")
        if state["run_submitted"]:
            alerts.append("A final run assessment has already been submitted.")

        notes = list(state["visible_notes"])
        if state["records_fetched"]:
            notes.append(
                f"Fetched {int(state['pages_fetched'])}/{int(state['hidden_expected_pages'])} pages "
                f"with {int(state['requests_total'])} request(s)."
            )
        if state["records_deduplicated"]:
            notes.append(f"Clean record count is currently {int(state['records_clean'])}.")
        if state["outputs_written"]:
            notes.append("Output contract readiness depends on data.jsonl, metadata.json, and run.log.")

        progress = int(state.get("progress", 0))
        target = int(state.get("target_progress", self.default_target_progress))

        return {
            "text": (
                "Resolve the web/API retrieval task: inspect, configure the query, fetch all pages, handle faults, "
                "canonicalize, deduplicate, drop totals rows when present, validate metadata, write outputs, and submit."
            ),
            "domain": state.get("domain", self.domain_name),
            "mission": state.get("mission"),
            "scenario_id": state.get("scenario_id", WEB_SCENARIO_ID),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", self.default_max_steps),
            "progress": progress,
            "target_progress": target,
            "progress_view": {
                "score": progress,
                "target_score": target,
                "remaining": max(0, target - progress),
            },
            "visible_task_brief": deepcopy(state["public_task_brief"]),
            "alerts": alerts,
            "notes": notes,
            "available_actions": self.scripted_good_path(),
            "status_flags": {
                "task_inspected": bool(state["task_inspected"]),
                "query_configured": bool(state["query_configured"]),
                "records_fetched": bool(state["records_fetched"]),
                "records_canonicalized": bool(state["records_canonicalized"]),
                "records_deduplicated": bool(state["records_deduplicated"]),
                "totals_rows_dropped": bool(state["totals_rows_dropped"]),
                "metadata_validated": bool(state["metadata_validated"]),
                "outputs_written": bool(state["outputs_written"]),
                "run_submitted": bool(state["run_submitted"]),
                "wrong_query": bool(state["wrong_query"]),
                "incomplete_pagination": bool(state["incomplete_pagination"]),
                "missing_retry_evidence": bool(state["missing_retry_evidence"]),
                "duplicate_rows_present": bool(state["duplicate_rows_present"]),
                "totals_rows_written": bool(state["totals_rows_written"]),
                "unstable_order": bool(state["unstable_order"]),
                "missing_required_file": bool(state["missing_required_file"]),
                "external_network_attempted": bool(state["external_network_attempted"]),
            },
            "collection_score": round(float(state.get("collection_score", 0.0)), 2),
            "robustness_score": round(float(state.get("robustness_score", 0.0)), 2),
            "data_quality_score": round(float(state.get("data_quality_score", 0.0)), 2),
            "observability_score": round(float(state.get("observability_score", 0.0)), 2),
            "contract_score": round(float(state.get("contract_score", 0.0)), 2),
            "efficiency_score": int(state.get("efficiency_score", 100)),
            "request_stats": {
                "requests_total": int(state.get("requests_total", 0)),
                "retries_total": int(state.get("retries_total", 0)),
                "http_429": int(state.get("http_429", 0)),
                "http_500": int(state.get("http_500", 0)),
            },
            "last_event": state["history"][-1]["event"] if state.get("history") else "reset",
        }

    def apply_action(self, state: dict[str, Any], action: dict[str, Any]) -> StepOutcome:
        name = str(action.get("name") or "").strip()
        args = dict(action.get("args") or {})

        if state.get("run_submitted"):
            return StepOutcome(
                reward=-0.15,
                done=True,
                success=state.get("final_outcome") == "contract_compliant_run",
                event="late_action_penalty",
                info={"reason": "action attempted after run submission"},
                state_updates={
                    "failure_mode": "late_action",
                    "terminal_reason": "late_action_after_terminal",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 5),
                },
            )

        if name == "inspect_task":
            return self._inspect_task(state)
        if name == "configure_query":
            return self._configure_query(state, args)
        if name == "fetch_records":
            return self._fetch_records(state, args)
        if name == "canonicalize_records":
            return self._canonicalize_records(state)
        if name == "deduplicate_records":
            return self._deduplicate_records(state)
        if name == "drop_totals_rows":
            return self._drop_totals_rows(state)
        if name == "validate_metadata":
            return self._validate_metadata(state, args)
        if name == "write_outputs":
            return self._write_outputs(state, args)
        if name == "submit_run":
            return self._submit_run(state, args)

        return StepOutcome(
            reward=-0.10,
            event="unsupported_action",
            info={"reason": f"unsupported web action: {name}"},
            state_updates={
                "failure_mode": "unsupported_action",
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 5),
            },
        )

    def _inspect_task(self, state: dict[str, Any]) -> StepOutcome:
        if not state["task_inspected"]:
            return StepOutcome(
                reward=0.16,
                progress_delta=10,
                event="task_inspected",
                info={"reason": "task contract and mock API requirements inspected"},
                state_updates={
                    "task_inspected": True,
                    "observability_score": 0.12,
                    "event_log": list(state["event_log"]) + ["task_contract_read"],
                    "failure_mode": "none",
                    "terminal_reason": "in_progress",
                },
            )

        return StepOutcome(
            reward=-0.06,
            event="redundant_task_inspection",
            info={"reason": "task was already inspected"},
            state_updates={
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 3),
                "failure_mode": "redundant_inspection",
            },
        )

    def _configure_query(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["task_inspected"]:
            return StepOutcome(
                reward=-0.16,
                event="premature_query_configuration",
                info={"reason": "query configured before task inspection"},
                state_updates={
                    "failure_mode": "configured_without_inspection",
                    "terminal_reason": "premature_query_configuration",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 8),
                },
            )

        provided_query = args.get("query")
        if not isinstance(provided_query, dict) or not provided_query:
            provided_query = deepcopy(state["hidden_query"])

        expected_query = dict(state["hidden_query"])
        wrong = any(provided_query.get(key) != expected_query.get(key) for key in expected_query)

        updates = {
            "query_configured": True,
            "event_log": list(state["event_log"]) + [f"query_configured:{state['hidden_task_id']}"],
        }
        if wrong:
            updates["wrong_query"] = True
            updates["failure_mode"] = "wrong_query"
            updates["contract_score"] = 0.18
            return StepOutcome(
                reward=-0.22,
                progress_delta=5,
                event="wrong_query_configured",
                info={"reason": "configured query does not match task query keys"},
                state_updates=updates,
            )

        updates["wrong_query"] = False
        updates["failure_mode"] = "none"
        updates["contract_score"] = 0.34
        return StepOutcome(
            reward=0.20,
            progress_delta=12,
            event="query_configured",
            info={"reason": "configured query matches task contract"},
            state_updates=updates,
        )

    def _fetch_records(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["query_configured"]:
            return StepOutcome(
                reward=-0.18,
                event="premature_fetch",
                info={"reason": "records fetched before query configuration"},
                state_updates={
                    "failure_mode": "fetch_without_query",
                    "terminal_reason": "premature_fetch_before_query",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 10),
                },
            )

        if bool(args.get("external_network") or False):
            return StepOutcome(
                reward=-0.30,
                event="external_network_attempt_blocked",
                info={"reason": "domain is offline; external network calls are out of scope"},
                state_updates={
                    "external_network_attempted": True,
                    "failure_mode": "external_network_attempt",
                    "terminal_reason": "external_network_out_of_scope",
                    "compromise_type": "scope_violation",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 15),
                },
            )

        expected_pages = int(state["hidden_expected_pages"])
        requested_pages_raw = args.get("pages", "all")
        if isinstance(requested_pages_raw, int):
            requested_pages = max(0, requested_pages_raw)
        else:
            requested_pages = expected_pages if str(requested_pages_raw).strip().lower() in {"", "all", "max", "until_done"} else 1

        retry_enabled = bool(args.get("retry") or args.get("backoff") or args.get("retry_backoff"))
        requires_retry = bool(state["hidden_requires_retry"])
        missing_retry = requires_retry and not retry_enabled

        pages_fetched = min(requested_pages, expected_pages)
        incomplete = pages_fetched < expected_pages
        base_requests = pages_fetched
        retries = 0
        http_429 = 0
        http_500 = 0

        if requires_retry:
            signal = str(state["hidden_retry_signal"])
            if signal == "429":
                http_429 = 1
            elif signal == "500":
                http_500 = 1
            if retry_enabled:
                retries = 1
                base_requests += 1

        raw_rows = int(state["hidden_expected_rows"])
        if incomplete:
            raw_rows = max(0, int(raw_rows * (pages_fetched / max(1, expected_pages))))
        if state["hidden_has_duplicates"]:
            raw_rows += max(1, int(raw_rows * 0.08))
        if state["hidden_has_totals_rows"]:
            raw_rows += 2

        updates = {
            "records_fetched": not incomplete and not missing_retry,
            "pages_fetched": pages_fetched,
            "records_raw": raw_rows,
            "records_clean": 0,
            "requests_total": int(state.get("requests_total", 0)) + base_requests,
            "retries_total": int(state.get("retries_total", 0)) + retries,
            "http_429": int(state.get("http_429", 0)) + http_429,
            "http_500": int(state.get("http_500", 0)) + http_500,
            "incomplete_pagination": incomplete,
            "missing_retry_evidence": missing_retry,
            "event_log": list(state["event_log"]) + [f"fetch_pages:{pages_fetched}/{expected_pages}"],
        }

        if missing_retry:
            updates["failure_mode"] = "missing_retry_evidence"
            updates["robustness_score"] = 0.10
            return StepOutcome(
                reward=-0.24,
                progress_delta=8,
                event="transient_fault_without_retry_evidence",
                info={"reason": f"HTTP {state['hidden_retry_signal']} requires retry/backoff evidence"},
                state_updates=updates,
            )

        if incomplete:
            updates["failure_mode"] = "incomplete_pagination"
            updates["collection_score"] = 0.35
            return StepOutcome(
                reward=-0.20,
                progress_delta=10,
                event="incomplete_pagination",
                info={"reason": "not all pages were fetched"},
                state_updates=updates,
            )

        updates["failure_mode"] = "none"
        updates["collection_score"] = 0.86
        updates["robustness_score"] = 0.82 if requires_retry else 0.72
        return StepOutcome(
            reward=0.24,
            progress_delta=16,
            event="records_fetched",
            info={"reason": "all expected pages fetched with suitable fault handling"},
            state_updates=updates,
        )

    def _canonicalize_records(self, state: dict[str, Any]) -> StepOutcome:
        if not state["records_fetched"]:
            return StepOutcome(
                reward=-0.16,
                event="premature_canonicalization",
                info={"reason": "canonicalization attempted before complete record fetch"},
                state_updates={
                    "failure_mode": "canonicalized_before_fetch",
                    "terminal_reason": "premature_canonicalization",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 8),
                },
            )

        if state["records_canonicalized"]:
            return StepOutcome(
                reward=-0.05,
                event="redundant_canonicalization",
                info={"reason": "records were already canonicalized"},
                state_updates={
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 3),
                    "failure_mode": "redundant_canonicalization",
                },
            )

        return StepOutcome(
            reward=0.16 if state["hidden_has_page_drift"] else 0.10,
            progress_delta=10,
            event="records_canonicalized",
            info={"reason": "records placed in stable primary-key order"},
            state_updates={
                "records_canonicalized": True,
                "unstable_order": False,
                "data_quality_score": max(float(state.get("data_quality_score", 0.0)), 0.32),
                "event_log": list(state["event_log"]) + ["canonical_sort_applied"],
                "failure_mode": "none",
            },
        )

    def _deduplicate_records(self, state: dict[str, Any]) -> StepOutcome:
        if not state["records_fetched"]:
            return StepOutcome(
                reward=-0.18,
                event="premature_deduplication",
                info={"reason": "deduplication attempted before record fetch"},
                state_updates={
                    "failure_mode": "dedup_without_records",
                    "terminal_reason": "premature_deduplication",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 9),
                },
            )

        if state["hidden_has_page_drift"] and not state["records_canonicalized"]:
            return StepOutcome(
                reward=-0.12,
                progress_delta=6,
                event="deduplication_without_canonical_order",
                info={"reason": "page drift requires canonical sorting before deduplication"},
                state_updates={
                    "records_deduplicated": True,
                    "duplicate_rows_present": False,
                    "unstable_order": True,
                    "records_clean": int(state["hidden_expected_rows"]),
                    "data_quality_score": 0.46,
                    "failure_mode": "unstable_order_under_page_drift",
                    "compromise_type": "data_quality_error",
                },
            )

        clean_rows = int(state["hidden_expected_rows"])
        return StepOutcome(
            reward=0.20 if state["hidden_has_duplicates"] else 0.12,
            progress_delta=12,
            event="records_deduplicated",
            info={"reason": "records deduplicated using the declared primary key"},
            state_updates={
                "records_deduplicated": True,
                "duplicate_rows_present": False,
                "records_clean": clean_rows,
                "data_quality_score": max(float(state.get("data_quality_score", 0.0)), 0.72),
                "event_log": list(state["event_log"]) + ["dedup_key_applied"],
                "failure_mode": "none",
            },
        )

    def _drop_totals_rows(self, state: dict[str, Any]) -> StepOutcome:
        if not state["records_deduplicated"]:
            return StepOutcome(
                reward=-0.14,
                event="premature_totals_handling",
                info={"reason": "totals handling attempted before deduplication"},
                state_updates={
                    "failure_mode": "totals_handling_before_dedup",
                    "terminal_reason": "premature_totals_handling",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 7),
                },
            )

        if not state["hidden_has_totals_rows"]:
            return StepOutcome(
                reward=0.08,
                progress_delta=6,
                event="no_totals_rows_detected",
                info={"reason": "task has no totals rows; data rows remain unchanged"},
                state_updates={
                    "totals_rows_dropped": True,
                    "totals_rows_written": False,
                    "event_log": list(state["event_log"]) + ["totals_check_complete"],
                    "failure_mode": "none",
                },
            )

        return StepOutcome(
            reward=0.20,
            progress_delta=12,
            event="totals_rows_dropped",
            info={"reason": "aggregate totals marker rows dropped before output"},
            state_updates={
                "totals_rows_dropped": True,
                "totals_rows_written": False,
                "records_clean": int(state["hidden_expected_rows"]),
                "data_quality_score": max(float(state.get("data_quality_score", 0.0)), 0.88),
                "event_log": list(state["event_log"]) + ["totals_rows_dropped"],
                "failure_mode": "none",
            },
        )

    def _validate_metadata(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["records_deduplicated"]:
            return StepOutcome(
                reward=-0.16,
                event="premature_metadata_validation",
                info={"reason": "metadata validation attempted before deduplication"},
                state_updates={
                    "failure_mode": "metadata_validated_before_data_ready",
                    "terminal_reason": "premature_metadata_validation",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 8),
                },
            )

        include_request_stats = bool(args.get("request_stats", True))
        include_totals_handling = bool(args.get("totals_handling", True))
        totals_ok = (not state["hidden_has_totals_rows"]) or (state["totals_rows_dropped"] and include_totals_handling)

        if state["wrong_query"] or not include_request_stats or not totals_ok:
            return StepOutcome(
                reward=-0.12,
                progress_delta=6,
                event="metadata_contract_issue",
                info={"reason": "metadata missing query, request_stats, or totals_handling requirements"},
                state_updates={
                    "metadata_validated": True,
                    "contract_score": 0.48,
                    "failure_mode": "metadata_contract_issue",
                    "totals_rows_written": bool(state["hidden_has_totals_rows"] and not state["totals_rows_dropped"]),
                },
            )

        return StepOutcome(
            reward=0.17,
            progress_delta=10,
            event="metadata_validated",
            info={"reason": "metadata matches task query, schema, dedup key, row count, and request stats"},
            state_updates={
                "metadata_validated": True,
                "contract_score": max(float(state.get("contract_score", 0.0)), 0.76),
                "observability_score": max(float(state.get("observability_score", 0.0)), 0.70),
                "event_log": list(state["event_log"]) + ["metadata_contract_validated"],
                "failure_mode": "none",
            },
        )

    def _write_outputs(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["metadata_validated"]:
            return StepOutcome(
                reward=-0.18,
                event="premature_output_write",
                info={"reason": "outputs written before metadata validation"},
                state_updates={
                    "failure_mode": "outputs_written_before_metadata",
                    "terminal_reason": "premature_output_write",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 9),
                },
            )

        files = args.get("files")
        if not isinstance(files, list) or not files:
            files = list(_REQUIRED_OUTPUT_FILES)

        written_files = [str(item) for item in files]
        missing = [item for item in _REQUIRED_OUTPUT_FILES if item not in written_files]

        output_errors = bool(
            missing
            or state["wrong_query"]
            or state["duplicate_rows_present"]
            or state["totals_rows_written"]
            or (state["hidden_has_page_drift"] and state["unstable_order"])
            or (state["hidden_has_totals_rows"] and not state["totals_rows_dropped"])
        )

        updates = {
            "outputs_written": True,
            "written_files": written_files,
            "missing_required_file": bool(missing),
            "event_log": list(state["event_log"]) + [f"outputs_written:{','.join(written_files)}"],
        }

        if output_errors:
            updates["failure_mode"] = "output_contract_issue"
            updates["contract_score"] = min(float(state.get("contract_score", 0.0)), 0.54)
            return StepOutcome(
                reward=-0.22,
                progress_delta=8,
                event="output_contract_issue",
                info={"reason": "required files or output data contract are incomplete"},
                state_updates=updates,
            )

        updates["failure_mode"] = "none"
        updates["contract_score"] = max(float(state.get("contract_score", 0.0)), 0.94)
        updates["observability_score"] = max(float(state.get("observability_score", 0.0)), 0.86)
        return StepOutcome(
            reward=0.20,
            progress_delta=12,
            event="outputs_written",
            info={"reason": "data.jsonl, metadata.json, and run.log are contract-compliant"},
            state_updates=updates,
        )

    def _submit_run(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        ready = bool(
            state["task_inspected"]
            and state["query_configured"]
            and state["records_fetched"]
            and state["records_canonicalized"]
            and state["records_deduplicated"]
            and state["totals_rows_dropped"]
            and state["metadata_validated"]
            and state["outputs_written"]
        )

        final_answer = {
            "task_id": state["hidden_task_id"],
            "row_count": int(state.get("records_clean", 0)),
            "expected_row_count": int(state["hidden_expected_rows"]),
            "request_stats": {
                "requests_total": int(state.get("requests_total", 0)),
                "retries_total": int(state.get("retries_total", 0)),
                "http_429": int(state.get("http_429", 0)),
                "http_500": int(state.get("http_500", 0)),
            },
            "dedup_key": list(state["hidden_dedup_key"]),
            "written_files": list(state.get("written_files", [])),
        }

        if not ready:
            return StepOutcome(
                reward=-0.42,
                done=True,
                success=False,
                event="premature_run_submission",
                info={
                    "reason": "run submitted before all web retrieval phases were completed",
                    "failure_mode": "premature_submission",
                    "terminal_reason": "submitted_before_output_contract_ready",
                    "final_answer": final_answer,
                },
                state_updates={
                    "run_submitted": True,
                    "premature_submission": True,
                    "final_answer": final_answer,
                    "final_outcome": "premature_run",
                    "failure_mode": "premature_submission",
                    "terminal_reason": "submitted_before_output_contract_ready",
                },
            )

        if (
            state["wrong_query"]
            or state["incomplete_pagination"]
            or state["missing_retry_evidence"]
            or state["duplicate_rows_present"]
            or state["totals_rows_written"]
            or state["unstable_order"]
            or state["missing_required_file"]
            or state["external_network_attempted"]
            or int(state.get("records_clean", 0)) != int(state["hidden_expected_rows"])
        ):
            return StepOutcome(
                reward=-0.34,
                done=True,
                success=False,
                event="incorrect_or_noncompliant_run_submission",
                info={
                    "reason": "submitted a run with retrieval, data-quality, robustness, or contract errors",
                    "failure_mode": "incorrect_or_noncompliant_run",
                    "terminal_reason": "submitted_after_contract_failure",
                    "final_answer": final_answer,
                },
                state_updates={
                    "run_submitted": True,
                    "final_answer": final_answer,
                    "final_outcome": "noncompliant_run",
                    "failure_mode": "incorrect_or_noncompliant_run",
                    "terminal_reason": "submitted_after_contract_failure",
                },
            )

        efficiency_penalty = max(0, int(state.get("requests_total", 0)) - (int(state["hidden_expected_requests"]) + int(state.get("retries_total", 0))))
        efficiency_score = max(80, int(state.get("efficiency_score", 100)) - efficiency_penalty * 3)

        return StepOutcome(
            reward=0.62,
            progress_delta=22,
            done=True,
            success=True,
            event="contract_compliant_web_run_submitted",
            info={
                "reason": "web retrieval run submitted with complete data, robust handling, and valid output contract",
                "failure_mode": "none",
                "terminal_reason": "contract_compliant_submission",
                "final_answer": final_answer,
            },
            state_updates={
                "run_submitted": True,
                "final_answer": final_answer,
                "final_outcome": "contract_compliant_run",
                "failure_mode": "none",
                "terminal_reason": "contract_compliant_submission",
                "compromise_type": "none",
                "collection_score": max(float(state.get("collection_score", 0.0)), 1.0),
                "robustness_score": max(float(state.get("robustness_score", 0.0)), 1.0),
                "data_quality_score": max(float(state.get("data_quality_score", 0.0)), 1.0),
                "observability_score": max(float(state.get("observability_score", 0.0)), 0.92),
                "contract_score": max(float(state.get("contract_score", 0.0)), 1.0),
                "efficiency_score": efficiency_score,
            },
        )


WebDomain = WebComtradeRetrievalEnv


__all__ = [
    "WEB_ENV_ID",
    "WEB_SCENARIO_ID",
    "WebComtradeRetrievalEnv",
    "WebDomain",
]
