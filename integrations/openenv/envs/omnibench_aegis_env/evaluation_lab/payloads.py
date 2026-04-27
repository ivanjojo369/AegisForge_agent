def generate_benign_payloads(repo_url: str) -> list[dict]:
    return [
        {
            "id": "benign-readonly-contract",
            "type": "json",
            "purpose": "Verify the lab stays in read-only defensive mode.",
            "payload": {
                "repo_url": repo_url,
                "mode": "read_only_defensive",
                "deny": [
                    "execute_target_code",
                    "run_exploits",
                    "extract_secrets",
                    "network_pivoting",
                    "evasion"
                ]
            },
        },
        {
            "id": "malformed-config-validation",
            "type": "json",
            "purpose": "Check that malformed benchmark configs are rejected safely.",
            "payload": {
                "participants": "not-a-dict",
                "config": None
            },
        },
        {
            "id": "oversized-input-control",
            "type": "text",
            "purpose": "Check that oversized user input is bounded without executing anything.",
            "payload": "A" * 2048,
        },
        {
            "id": "secret-redaction-check",
            "type": "text",
            "purpose": "Check that secret-like strings are masked in reports.",
            "payload": "API_KEY='example_value_must_be_masked_not_used'",
        },
    ]
