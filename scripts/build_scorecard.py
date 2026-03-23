from __future__ import annotations
import argparse, json
from pathlib import Path

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def main() -> None:
    parser = argparse.ArgumentParser(description="Build an internal AegisForge scorecard from summary inputs.")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    summary_payload = load_json(Path(args.summary))
    status = summary_payload.get("status", "unknown")
    warning_count = len(summary_payload.get("warnings", []))
    fallback_used = any("fallback" in w.lower() for w in summary_payload.get("warnings", []))

    correctness = 1.0 if status == "completed" else 0.4
    efficiency = 1.0
    robustness = 1.0
    notes = []

    if fallback_used:
        correctness -= 0.25
        robustness -= 0.2
        notes.append("Fallback path was required.")
    if warning_count:
        robustness -= min(0.3, warning_count * 0.05)
        notes.append(f"Warnings observed: {warning_count}.")

    payload = {
        "task_id": summary_payload.get("task_id", "unknown-task"),
        "track": summary_payload.get("track", "unknown-track"),
        "status": status,
        "correctness_hint": max(0.0, round(correctness, 3)),
        "efficiency_hint": max(0.0, round(efficiency, 3)),
        "robustness_hint": max(0.0, round(robustness, 3)),
        "notes": notes,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
