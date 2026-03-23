from __future__ import annotations

import argparse
import json

from .agent_card import agent_card_response_dict
from .config import AppConfig
from .health import health_response_dict
from .logging import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AegisForge runtime helper CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("print-config", help="Print resolved runtime configuration as JSON.")
    subparsers.add_parser("print-health", help="Print the health payload as JSON.")
    subparsers.add_parser("print-agent-card", help="Print the Agent Card payload as JSON.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = AppConfig.from_env()
    setup_logging(config.log_level)

    if args.command == "print-config":
        print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "print-health":
        print(json.dumps(health_response_dict(config), indent=2, ensure_ascii=False))
        return 0

    if args.command == "print-agent-card":
        print(json.dumps(agent_card_response_dict(config), indent=2, ensure_ascii=False))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
