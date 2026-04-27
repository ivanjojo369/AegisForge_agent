from .payloads import generate_benign_payloads
from .scenarios import generate_controlled_scenarios


REPORT_VERSION = "0.1.2"


def build_report(scan: dict) -> dict:
    scenarios = generate_controlled_scenarios(scan)
    payloads = generate_benign_payloads(scan["repo_url"])

    report = {
        "lab": "AegisForge Evaluation Lab",
        "version": REPORT_VERSION,
        "mode": "read_only_defensive",
        "repo_url": scan["repo_url"],
        "risk_score": scan["risk_score"],
        "risk_tier": scan.get("risk_tier"),
        "review_load": scan.get("review_load"),
        "files_analyzed": scan["files_analyzed"],
        "repo_shape": scan["repo_shape"],
        "scan_limits": scan.get("scan_limits", {}),
        "finding_summary": scan.get("finding_summary", {}),
        "findings": scan["findings"],
        "controlled_scenarios": scenarios,
        "benign_payloads": payloads,
        "classifier_version": scan.get("classifier_version", REPORT_VERSION),
        "precision_notes": scan.get("precision_notes", []),
        "agentbeats_alignment": {
            "green_agent_role": "assessor / evaluator",
            "a2a_ready_focus": True,
            "mcp_tooling_focus": True,
            "reproducibility_focus": True,
            "no_real_exploitation": True,
        },
        "recommended_next_steps": [
            "Keep this report artifact visible in the Space UI.",
            "Add CI smoke tests for /api/evaluation-lab/analyze.",
            "Document that the lab performs defensive static analysis only.",
            "Review high findings manually before treating them as confirmed issues.",
        ],
    }

    # Avoid null values for older scanner payloads.
    report["risk_tier"] = report["risk_tier"] or _risk_tier_for_score(int(report["risk_score"] or 0))
    report["review_load"] = report["review_load"] or {
        "level": "unknown",
        "finding_count": len(report["findings"]),
        "files_analyzed": report["files_analyzed"],
        "truncated": bool(report["scan_limits"].get("file_limit_reached")),
    }
    return report


def _risk_tier_for_score(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "elevated"
    if score >= 25:
        return "moderate"
    return "low"
