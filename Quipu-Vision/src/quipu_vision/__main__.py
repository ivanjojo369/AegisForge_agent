from __future__ import annotations

import argparse
import json
from .core import inspect


def main() -> None:
    parser = argparse.ArgumentParser(description="Quipu Vision: verify image changes against a reference")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--observed", required=True)
    parser.add_argument("--followup")
    parser.add_argument("--annotated")
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--threshold", type=int, default=32)
    args = parser.parse_args()
    report = inspect(args.reference, args.observed,
                     followup_path=args.followup, roi=tuple(args.roi) if args.roi else None,
                     threshold=args.threshold, annotated_path=args.annotated)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
