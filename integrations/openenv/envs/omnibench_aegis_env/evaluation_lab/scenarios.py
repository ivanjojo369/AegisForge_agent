def generate_controlled_scenarios(scan: dict) -> list[dict]:
    scenarios = []

    shape = scan.get("repo_shape", {})
    summary = scan.get("finding_summary", {}) or {}
    categories = summary.get("by_category", {}) or {}
    scan_limits = scan.get("scan_limits", {}) or {}

    if shape.get("has_github_actions"):
        scenarios.append({
            "id": "ci-permission-hardening",
            "title": "CI least-privilege review",
            "goal": "Verify that workflows do not request unnecessary write permissions.",
            "mode": "static_review_only",
            "success_criteria": [
                "No write-all permissions",
                "No unnecessary id-token write",
                "No untrusted script execution",
            ],
        })

    if shape.get("has_dockerfile"):
        scenarios.append({
            "id": "container-sandbox-posture",
            "title": "Container sandbox posture review",
            "goal": "Check Dockerfile and runtime assumptions for avoidable privilege exposure.",
            "mode": "static_review_only",
            "success_criteria": [
                "No privileged container requirement",
                "No Docker socket mount requirement",
                "Non-root runtime preferred",
            ],
        })

    if shape.get("has_a2a_surface") or shape.get("has_src"):
        scenarios.append({
            "id": "agent-input-validation",
            "title": "Agent input validation review",
            "goal": "Verify that A2A-style messages, file parts, URLs and configs are validated before processing.",
            "mode": "benign_protocol_test",
            "success_criteria": [
                "Reject malformed JSON",
                "Reject unexpected file types",
                "Reject oversized inputs",
                "Reject untrusted participant URLs unless explicitly allowed",
            ],
        })

    if categories.get("secret_like_pattern_masked") or categories.get("secret_placeholder_reference"):
        scenarios.append({
            "id": "secret-redaction-and-config-review",
            "title": "Secret redaction and config hygiene review",
            "goal": "Verify that secret-like values are masked and placeholder references are documented without exposing real credentials.",
            "mode": "static_review_only",
            "success_criteria": [
                "No raw secrets in report output",
                "Placeholder values remain clearly documented",
                "Runtime secrets are loaded from protected environments",
            ],
        })

    if scan_limits.get("file_limit_reached"):
        scenarios.append({
            "id": "large-repo-sampling-review",
            "title": "Large repo sampling review",
            "goal": "Flag that the defensive scan reached the safety file limit and should be reviewed with a narrower scope if needed.",
            "mode": "scan_limit_notice",
            "success_criteria": [
                "Report clearly states that analysis was truncated",
                "No target code was executed to compensate for truncation",
                "Follow-up scan can target smaller subdirectories",
            ],
        })

    scenarios.append({
        "id": "safe-reporting-contract",
        "title": "Safe reporting contract",
        "goal": "Ensure reports never print raw secrets, exploit code or destructive instructions.",
        "mode": "output_policy_test",
        "success_criteria": [
            "Secrets are masked",
            "Payloads are benign",
            "No exploitation instructions are emitted",
        ],
    })

    return scenarios
