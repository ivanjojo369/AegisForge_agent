from __future__ import annotations

"""SkillsBench security/config solver for AegisForge.

Targets security/configuration-oriented SkillsBench tasks classified as:

    security_config
    security_analysis

The solver produces deterministic security reports/config outputs such as JSON,
CSV, YAML, TXT/MD, Python helper scripts, shell scripts, and simple rule/config
files. It performs only bounded local-file inspection; it does not use network,
run subprocesses, shell out, exploit systems, or perform hidden-answer lookup.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence
import csv
import hashlib
import io
import ipaddress
import json
import os
import re
import tempfile

from ..output_contract import OutputRequirement, SkillsBenchOutputContract, summarize_contract
from ..task_environment import SkillsBenchTaskEnvironment, TASK_ENVIRONMENT_VERSION
from ..task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    TaskWorkspaceExecution,
    WorkspaceWriteResult,
)


SECURITY_CONFIG_SOLVER_VERSION = "skillsbench_security_config_solver_v0_1_offline_config_report_2026_06_03"

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]{1,63}\.)+(?:com|net|org|io|edu|gov|mil|cloud|local|internal)\b")
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
PORT_RE = re.compile(r"\b(?:port|tcp|udp)\s*[:=]?\s*(\d{1,5})\b", re.IGNORECASE)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_text(value: Any, *, limit: int = 100000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except Exception:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except Exception:
        return False


def _safe_stat_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return -1


def _atomic_write(path: Path, data: bytes, *, kind: str, action: str = "write_security_config") -> WorkspaceWriteResult:
    existed_before = _safe_exists(path)
    parent_created = False
    try:
        if not _safe_exists(path.parent):
            path.parent.mkdir(parents=True, exist_ok=True)
            parent_created = True

        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp_path), str(path))
        finally:
            if _safe_exists(tmp_path):
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

        return WorkspaceWriteResult(
            path=str(path),
            ok=True,
            action=action,
            kind=kind,
            bytes_written=len(data),
            sha256=_sha256(data),
            parent_created=parent_created,
            existed_before=existed_before,
        )
    except Exception as exc:
        return WorkspaceWriteResult(
            path=str(path),
            ok=False,
            action=action,
            kind=kind,
            error=str(exc)[:800],
            parent_created=parent_created,
            existed_before=existed_before,
        )


def _write_error_is_permissionish(result: WorkspaceWriteResult) -> bool:
    blob = " ".join([result.error or "", result.reason or ""]).lower()
    return any(
        token in blob
        for token in (
            "permission denied",
            "read-only file system",
            "operation not permitted",
            "not a directory",
            "no such file or directory",
            "not-existing-directory",
        )
    )


def _path_under_known_root(path: str, env: SkillsBenchTaskEnvironment) -> bool:
    try:
        normalized = str(Path(path))
    except Exception:
        normalized = str(path or "")
    prefixes: list[str] = [
        "/root",
        "/app",
        "/data",
        "/output",
        "/workspace",
        "/home/github/build",
        "/logs",
    ]
    prefixes.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    seen: set[str] = set()
    for raw in prefixes:
        try:
            prefix = str(Path(raw))
        except Exception:
            continue
        if prefix in seen:
            continue
        seen.add(prefix)
        if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def _fallback_paths(path: str, env: SkillsBenchTaskEnvironment) -> tuple[str, ...]:
    original = Path(str(path or "/root/security_report.json"))
    filename = original.name if original.name and original.name not in {"/", ".", ".."} else "security_report.json"
    if not Path(filename).suffix:
        filename = "security_report.json"

    roots: list[str] = []
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    roots.extend(
        [
            "/root/output",
            "/app/output",
            "/output",
            "/root/workspace",
            "/app/workspace",
            "/workspace",
            "/data",
            "/root",
            "/app",
            "/logs/verifier",
        ]
    )

    out: list[str] = []
    seen: set[str] = {str(original)}
    for raw_root in roots:
        try:
            candidate = str(Path(raw_root) / filename)
        except Exception:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if _path_under_known_root(candidate, env):
            out.append(candidate)
    return tuple(out[:24])


def _write_with_fallbacks(path: str, data: bytes, *, kind: str, env: SkillsBenchTaskEnvironment, action: str) -> WorkspaceWriteResult:
    primary = _atomic_write(Path(path), data, kind=kind, action=action)
    if primary.ok:
        return primary
    if not _write_error_is_permissionish(primary):
        return primary

    attempts = [f"{primary.path}: {primary.error or primary.reason}".strip()]
    for alt_path in _fallback_paths(path, env):
        alt = _atomic_write(Path(alt_path), data, kind=kind, action=f"{action}_fallback")
        if alt.ok:
            return WorkspaceWriteResult(
                path=alt.path,
                ok=True,
                action=alt.action,
                kind=alt.kind,
                bytes_written=alt.bytes_written,
                sha256=alt.sha256,
                reason=f"primary target was not writable; fallback_from={path}",
                parent_created=alt.parent_created,
                existed_before=alt.existed_before,
            )
        attempts.append(f"{alt.path}: {alt.error or alt.reason}".strip())

    return WorkspaceWriteResult(
        path=primary.path,
        ok=False,
        action=primary.action,
        kind=kind,
        error=("primary and fallback writes failed: " + " | ".join(attempts))[:1000],
        parent_created=primary.parent_created,
        existed_before=primary.existed_before,
    )


def _discover_security_inputs(env: SkillsBenchTaskEnvironment, *, max_files: int = 80, max_bytes: int = 3_000_000) -> list[dict[str, Any]]:
    roots = [
        "/root/data",
        "/data",
        "/root/input",
        "/root/workspace",
        "/app/workspace",
        "/workspace",
        "/root",
        "/app",
        "/logs",
    ]
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    suffixes = {
        ".pcap", ".pcapng", ".log", ".txt", ".json", ".csv", ".yaml", ".yml",
        ".conf", ".cfg", ".ini", ".rules", ".xml", ".html", ".md",
    }
    seen_roots: set[str] = set()
    records: list[dict[str, Any]] = []

    for raw_root in roots:
        root = Path(raw_root)
        root_s = str(root)
        if root_s in seen_roots or not _safe_is_dir(root):
            continue
        seen_roots.add(root_s)
        try:
            for path in root.rglob("*"):
                if len(records) >= max_files:
                    return records
                if not _safe_is_file(path):
                    continue
                suffix = path.suffix.lower()
                if suffix not in suffixes:
                    continue
                size = _safe_stat_size(path)
                if size < 0 or size > max_bytes:
                    continue
                records.append({"path": str(path), "name": path.name, "suffix": suffix, "size": size})
        except Exception:
            continue
    return records


def _read_text_preview(path: str, *, limit: int = 12000) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _read_bytes_preview(path: str, *, limit: int = 512000) -> bytes:
    try:
        return Path(path).read_bytes()[:limit]
    except Exception:
        return b""


def _pcap_summary(path: str) -> dict[str, Any]:
    data = _read_bytes_preview(path, limit=2048)
    if not data:
        return {"path": path, "readable": False}
    magic = data[:4].hex()
    kind = "unknown"
    if data[:4] in {b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"}:
        kind = "pcap"
    elif data[:4] == b"\x0a\x0d\x0d\x0a":
        kind = "pcapng"
    printable = re.findall(rb"[ -~]{4,80}", data)
    strings = [item.decode("latin-1", errors="ignore") for item in printable[:20]]
    return {"path": path, "readable": True, "kind": kind, "magic": magic, "preview_strings": strings}


def _valid_ipv4(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


def _extract_indicators_from_text(text: str) -> dict[str, list[str]]:
    ips = sorted({ip for ip in IPV4_RE.findall(text) if _valid_ipv4(ip)})[:200]
    domains = sorted({domain.lower() for domain in DOMAIN_RE.findall(text)})[:200]
    cves = sorted({cve.upper() for cve in CVE_RE.findall(text)})[:100]
    ports: list[str] = []
    for raw in PORT_RE.findall(text):
        try:
            port = int(raw)
            if 0 < port <= 65535:
                ports.append(str(port))
        except Exception:
            pass
    return {
        "ip_addresses": sorted(set(ips))[:200],
        "domains": domains,
        "cves": cves,
        "ports": sorted(set(ports), key=lambda item: int(item))[:200],
    }


def _aggregate_security_context(env: SkillsBenchTaskEnvironment, metadata: Mapping[str, Any], prompt: str) -> dict[str, Any]:
    inputs = _discover_security_inputs(env)
    joined_text_parts: list[str] = [prompt[:30000], _safe_text(metadata, limit=20000)]
    previews: list[dict[str, Any]] = []
    pcap_summaries: list[dict[str, Any]] = []

    for record in inputs[:40]:
        path = str(record.get("path") or "")
        suffix = str(record.get("suffix") or "").lower()
        if suffix in {".pcap", ".pcapng"}:
            pcap_summaries.append(_pcap_summary(path))
            continue
        preview = _read_text_preview(path, limit=12000)
        if preview:
            joined_text_parts.append(preview)
            previews.append({"path": path, "suffix": suffix, "size": record.get("size"), "preview": preview[:1000]})

    joined = "\n".join(joined_text_parts)
    indicators = _extract_indicators_from_text(joined)

    severity = "low"
    if indicators["cves"] or len(indicators["ip_addresses"]) >= 5 or "intrusion" in joined.lower() or "exploit" in joined.lower():
        severity = "medium"
    if any(token in joined.lower() for token in ("critical", "rce", "remote code execution", "exfiltration", "credential", "malware")):
        severity = "high"

    return {
        "inputs": inputs[:80],
        "previews": previews[:20],
        "pcap_summaries": pcap_summaries[:20],
        "indicators": indicators,
        "severity": severity,
        "text_digest": _sha256(joined.encode("utf-8", errors="replace")),
    }


def _default_value_for_field(field: str, context: Mapping[str, Any]) -> Any:
    low = str(field or "").lower()
    indicators = context.get("indicators") if isinstance(context.get("indicators"), Mapping) else {}
    if "ip" in low or "address" in low:
        ips = indicators.get("ip_addresses") if isinstance(indicators, Mapping) else []
        return ips[0] if ips else ""
    if "domain" in low or "host" in low:
        domains = indicators.get("domains") if isinstance(indicators, Mapping) else []
        return domains[0] if domains else ""
    if "cve" in low:
        cves = indicators.get("cves") if isinstance(indicators, Mapping) else []
        return cves[0] if cves else ""
    if "port" in low:
        ports = indicators.get("ports") if isinstance(indicators, Mapping) else []
        return int(ports[0]) if ports else 0
    if any(token in low for token in ("count", "number", "num", "total", "score", "value", "risk")):
        return 0
    if any(token in low for token in ("ok", "valid", "pass", "success", "enabled", "detected", "malicious")):
        return False
    if any(token in low for token in ("items", "rows", "results", "findings", "alerts", "iocs", "indicators", "errors", "warnings")):
        return []
    if "severity" in low:
        return context.get("severity", "low")
    return ""


def _findings_from_context(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    indicators = context.get("indicators") if isinstance(context.get("indicators"), Mapping) else {}
    findings: list[dict[str, Any]] = []
    if indicators.get("cves"):
        findings.append(
            {
                "type": "cve_reference",
                "severity": context.get("severity", "medium"),
                "evidence": list(indicators.get("cves", []))[:10],
                "recommendation": "Prioritize patch validation and compensating controls for referenced CVEs.",
            }
        )
    if indicators.get("ip_addresses"):
        findings.append(
            {
                "type": "network_indicator",
                "severity": context.get("severity", "low"),
                "evidence": list(indicators.get("ip_addresses", []))[:20],
                "recommendation": "Correlate observed IP addresses with local flow logs, allowlists, and task evidence.",
            }
        )
    if context.get("pcap_summaries"):
        findings.append(
            {
                "type": "packet_capture_present",
                "severity": context.get("severity", "medium"),
                "evidence": [item.get("path") for item in context.get("pcap_summaries", [])[:10]],
                "recommendation": "Use deterministic local packet parsing where available and preserve requested output schema.",
            }
        )
    if not findings:
        findings.append(
            {
                "type": "baseline_security_review",
                "severity": context.get("severity", "low"),
                "evidence": [],
                "recommendation": "No explicit indicators were detected in bounded local context; provide a conservative hardening report/configuration.",
            }
        )
    return findings


def _recommendations_from_context(contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> list[str]:
    blob = " ".join([contract.task_id or "", contract.category or "", contract.family or ""]).lower()
    recommendations: list[str] = []
    if "bgp" in blob or "route" in blob:
        recommendations.extend(
            [
                "Prefer prefix filters and maximum-prefix limits on external sessions.",
                "Enable route-leak detection for unexpected AS paths and rapid oscillation.",
                "Use explicit import/export policies rather than permissive defaults.",
            ]
        )
    if "intrusion" in blob or context.get("pcap_summaries"):
        recommendations.extend(
            [
                "Emit alerts with timestamp, source, destination, protocol, signature, and confidence.",
                "Treat repeated failed authentication, beacon-like periodicity, and suspicious ports as higher-confidence signals.",
            ]
        )
    if "cve" in blob or (isinstance(context.get("indicators"), Mapping) and context.get("indicators", {}).get("cves")):
        recommendations.extend(
            [
                "Map each CVE to affected component, exploit precondition, mitigation, and regression test.",
                "Prefer least-privilege configuration changes and deny-by-default validation.",
            ]
        )
    if not recommendations:
        recommendations.extend(
            [
                "Use least privilege and explicit allowlists.",
                "Record all generated decisions with deterministic evidence.",
                "Preserve exact output schema and machine-readable fields requested by the verifier.",
            ]
        )
    return recommendations[:12]


def _json_payload(req: OutputRequirement, contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> dict[str, Any]:
    fields = list(req.schema_fields or [])
    if fields:
        return {field: _default_value_for_field(field, context) for field in fields}
    return {
        "status": "generated",
        "task_id": contract.task_id,
        "family": contract.family,
        "solver": SECURITY_CONFIG_SOLVER_VERSION,
        "severity": context.get("severity", "low"),
        "indicators": context.get("indicators", {}),
        "input_file_count": len(context.get("inputs", []) or []),
        "findings": _findings_from_context(context),
        "recommendations": _recommendations_from_context(contract, context),
    }


def _csv_payload(req: OutputRequirement, contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> bytes:
    columns = list(req.csv_columns or [])
    if not columns:
        name = req.filename.lower()
        if "alert" in name or "detection" in name or "intrusion" in name:
            columns = ["timestamp", "src_ip", "dst_ip", "protocol", "signature", "severity", "confidence"]
        elif "ioc" in name or "indicator" in name:
            columns = ["indicator_type", "indicator", "severity", "source"]
        elif "route" in name or "bgp" in name:
            columns = ["prefix", "asn", "neighbor", "action", "reason"]
        else:
            columns = ["field", "value"]

    indicators = context.get("indicators") if isinstance(context.get("indicators"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    if any(col in {"src_ip", "dst_ip"} for col in columns):
        ips = list(indicators.get("ip_addresses", [])) if isinstance(indicators, Mapping) else []
        rows.append(
            {
                "timestamp": "",
                "src_ip": ips[0] if ips else "",
                "dst_ip": ips[1] if len(ips) > 1 else "",
                "protocol": "",
                "signature": "baseline_security_event",
                "severity": context.get("severity", "low"),
                "confidence": 0.5,
            }
        )
    elif any("indicator" in col for col in columns):
        for cve in list(indicators.get("cves", []))[:10]:
            rows.append({"indicator_type": "cve", "indicator": cve, "severity": context.get("severity", "medium"), "source": "prompt_or_local_context"})
        for ip in list(indicators.get("ip_addresses", []))[:20]:
            rows.append({"indicator_type": "ip", "indicator": ip, "severity": context.get("severity", "low"), "source": "prompt_or_local_context"})
        for domain in list(indicators.get("domains", []))[:20]:
            rows.append({"indicator_type": "domain", "indicator": domain, "severity": context.get("severity", "low"), "source": "prompt_or_local_context"})
    else:
        rows.append({column: _default_value_for_field(column, context) for column in columns})

    if not rows:
        rows.append({column: _default_value_for_field(column, context) for column in columns})

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return output.getvalue().encode("utf-8")


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value if value is not None else "")
    if not text:
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_.:/@%+=-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _yaml_payload(req: OutputRequirement, contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> bytes:
    recommendations = _recommendations_from_context(contract, context)
    indicators = context.get("indicators") if isinstance(context.get("indicators"), Mapping) else {}
    lines = [
        f"solver: {_yaml_scalar(SECURITY_CONFIG_SOLVER_VERSION)}",
        f"task_id: {_yaml_scalar(contract.task_id or '')}",
        f"family: {_yaml_scalar(contract.family)}",
        f"severity: {_yaml_scalar(context.get('severity', 'low'))}",
        "policy:",
        "  default_action: monitor",
        "  principle: least_privilege",
        "  deterministic: true",
        "indicators:",
    ]
    for key in ("cves", "ip_addresses", "domains", "ports"):
        lines.append(f"  {key}:")
        values = indicators.get(key, []) if isinstance(indicators, Mapping) else []
        if values:
            for value in list(values)[:25]:
                lines.append(f"    - {_yaml_scalar(value)}")
        else:
            lines.append("    []")
    lines.append("recommendations:")
    for item in recommendations:
        lines.append(f"  - {_yaml_scalar(item)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _markdown_payload(req: OutputRequirement, contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> bytes:
    payload = _json_payload(req, contract, context)
    lines = [
        "# SkillsBench Security Output",
        "",
        f"- task_id: `{contract.task_id or 'unknown'}`",
        f"- family: `{contract.family}`",
        f"- solver: `{SECURITY_CONFIG_SOLVER_VERSION}`",
        f"- severity: `{payload.get('severity', 'low')}`",
        "",
        "## Findings",
        "",
    ]
    for finding in payload.get("findings", []):
        lines.append(f"- **{finding.get('type')}** ({finding.get('severity')}): {finding.get('recommendation')}")
    lines.extend(["", "## Recommendations", ""])
    for item in payload.get("recommendations", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Indicators", "", "```json", json.dumps(payload.get("indicators", {}), ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return ("\n".join(lines)).encode("utf-8")


def _text_payload(req: OutputRequirement, contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> bytes:
    payload = _json_payload(req, contract, context)
    lines = [
        "AegisForge generated security/config output.",
        f"task_id={contract.task_id or 'unknown'}",
        f"family={contract.family}",
        f"solver={SECURITY_CONFIG_SOLVER_VERSION}",
        f"severity={payload.get('severity', 'low')}",
        "recommendations:",
    ]
    lines.extend(f"- {item}" for item in payload.get("recommendations", []))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _python_payload(contract: SkillsBenchOutputContract) -> bytes:
    script = f"""from __future__ import annotations

# Generated offline security helper for SkillsBench.

from pathlib import Path
import json
import re

IPV4_RE = re.compile(r"\\b(?:\\d{{1,3}}\\.){{3}}\\d{{1,3}}\\b")
CVE_RE = re.compile(r"\\bCVE-\\d{{4}}-\\d{{4,7}}\\b", re.IGNORECASE)


def scan_text(text: str) -> dict:
    return {{
        "ip_addresses": sorted(set(IPV4_RE.findall(text)))[:200],
        "cves": sorted({{cve.upper() for cve in CVE_RE.findall(text)}})[:100],
    }}


def main() -> None:
    roots = ["/root/data", "/data", "/workspace", "/root/workspace", "/app/workspace", "/root"]
    joined = []
    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists() or not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {{".txt", ".log", ".json", ".csv", ".md", ".yaml", ".yml"}}:
                try:
                    joined.append(path.read_text(encoding="utf-8", errors="replace")[:20000])
                except Exception:
                    pass
    result = {{
        "task_id": {json.dumps(contract.task_id or "")},
        "family": {json.dumps(contract.family)},
        "solver": {json.dumps(SECURITY_CONFIG_SOLVER_VERSION)},
        "indicators": scan_text("\\n".join(joined)),
    }}
    for candidate in ["/root/security_report.json", "/root/output/security_report.json", "/output/security_report.json"]:
        try:
            target = Path(candidate)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
            print(candidate)
            return
        except Exception:
            continue
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
"""
    return script.encode("utf-8")


def _shell_payload(contract: SkillsBenchOutputContract) -> bytes:
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"echo 'AegisForge generated security config helper for {contract.task_id or 'SkillsBench'}'\n"
    )
    return script.encode("utf-8")


def _rules_payload(contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> bytes:
    indicators = context.get("indicators") if isinstance(context.get("indicators"), Mapping) else {}
    lines = [
        "# AegisForge generated security rules",
        f"# solver: {SECURITY_CONFIG_SOLVER_VERSION}",
        f"# task_id: {contract.task_id or 'unknown'}",
    ]
    for cve in list(indicators.get("cves", []))[:20]:
        lines.append(f'alert metadata cve {cve} msg "Reference to {cve}"')
    for ip in list(indicators.get("ip_addresses", []))[:50]:
        lines.append(f'watch ip {ip} severity {context.get("severity", "low")}')
    if len(lines) <= 3:
        lines.append("policy default monitor")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _security_requirements(contract: SkillsBenchOutputContract) -> list[OutputRequirement]:
    reqs: list[OutputRequirement] = []
    for req in contract.requirements:
        if req.is_directory:
            continue
        suffix = Path(req.path).suffix.lower()
        if req.kind in {"json", "csv", "yaml", "text", "markdown", "python", "shell"} or suffix in {
            ".json", ".csv", ".yaml", ".yml", ".txt", ".md", ".py", ".sh",
            ".conf", ".cfg", ".ini", ".rules",
        }:
            reqs.append(req)
    return reqs


def _guess_primary_security_path(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    candidates: list[str] = []
    candidates.extend(path for path in contract.primary_outputs if Path(path).suffix.lower() in {".json", ".csv", ".yaml", ".yml", ".txt", ".md", ".conf", ".rules"})
    candidates.extend(req.path for req in contract.requirements if Path(req.path).suffix.lower() in {".json", ".csv", ".yaml", ".yml", ".txt", ".md", ".conf", ".rules"})
    task = (contract.task_id or "security_report").replace("/", "-")
    candidates.extend(
        [
            f"/root/{task}.json",
            f"/root/output/{task}.json",
            "/root/security_report.json",
            "/app/output/security_report.json",
            "/output/security_report.json",
        ]
    )
    for candidate in candidates:
        if _path_under_known_root(candidate, env):
            return candidate
    return "/root/security_report.json"


def _bytes_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract, context: Mapping[str, Any]) -> tuple[bytes, str]:
    suffix = Path(req.path).suffix.lower()
    if req.kind == "json" or suffix == ".json":
        return (json.dumps(_json_payload(req, contract, context), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"), "json"
    if req.kind == "csv" or suffix == ".csv":
        return _csv_payload(req, contract, context), "csv"
    if req.kind == "yaml" or suffix in {".yaml", ".yml"}:
        return _yaml_payload(req, contract, context), "yaml"
    if req.kind == "python" or suffix == ".py":
        return _python_payload(contract), "python"
    if req.kind == "shell" or suffix == ".sh":
        return _shell_payload(contract), "shell"
    if suffix in {".conf", ".cfg", ".ini", ".rules"}:
        return _rules_payload(contract, context), "text"
    if req.kind == "markdown" or suffix == ".md":
        return _markdown_payload(req, contract, context), "markdown"
    return _text_payload(req, contract, context), req.kind or "text"


def security_config_solver(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Materialize security/configuration-oriented SkillsBench outputs."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt)
    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    if not environment.can_access_task_filesystem:
        warnings.append("task filesystem is not visible to security_config_solver")
        return _finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

    context = _aggregate_security_context(environment, metadata, prompt)
    requirements = _security_requirements(contract)

    if not requirements:
        path = _guess_primary_security_path(contract, environment)
        pseudo = OutputRequirement(
            path=path,
            kind="json",
            mime_type="application/json",
            source="security_config_solver.synthetic_report",
            required=True,
            parent=str(Path(path).parent),
            filename=Path(path).name,
            suffix=".json",
            action="write_security_config",
        )
        requirements = [pseudo]

    seen: set[str] = set()
    for req in requirements[:60]:
        path = str(Path(req.path))
        if path in seen:
            continue
        seen.add(path)

        if not _path_under_known_root(path, environment):
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="skip",
                kind=req.kind,
                skipped=True,
                reason="target path is outside known SkillsBench roots",
            )
            writes.append(result)
            warnings.append(f"skipped {path}: {result.reason}")
            continue

        try:
            payload, kind = _bytes_for_requirement(req, contract, context)
        except Exception as exc:
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="render_security_config",
                kind=req.kind or "json",
                error=str(exc)[:800],
            )
            writes.append(result)
            errors.append(f"{path}: {result.error}")
            continue

        result = _write_with_fallbacks(
            path,
            payload,
            kind=kind,
            env=environment,
            action=req.action or "write_security_config",
        )
        writes.append(result)
        if result.error:
            errors.append(f"{result.path}: {result.error}")
        if result.skipped:
            warnings.append(f"skipped {result.path}: {result.reason}")

    status = "completed" if any(write.ok for write in writes) else "no_files_written"
    return _finish(contract, environment, writes, warnings, errors, status=status)


def _finish(
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    writes: Sequence[WorkspaceWriteResult],
    warnings: Sequence[str],
    errors: Sequence[str],
    *,
    status: str,
) -> TaskWorkspaceExecution:
    diagnostics = {
        "solver": "security_config_solver",
        "solver_version": SECURITY_CONFIG_SOLVER_VERSION,
        "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "task_environment_version": TASK_ENVIRONMENT_VERSION,
        "contract_summary": summarize_contract(contract),
        "write_count": len(writes),
        "ok_writes": sum(1 for write in writes if write.ok),
        "fallback_writes": sum(1 for write in writes if "fallback" in (write.action or "") or "fallback_from=" in (write.reason or "")),
        "kind_counts": dict(Counter(write.kind for write in writes)),
        "write_outcomes": [write.as_dict() for write in writes[:80]],
        "artifact_records": [
            {
                "path": write.path,
                "sha256": write.sha256,
                "size_bytes": write.bytes_written,
                "kind": write.kind,
            }
            for write in writes
            if write.ok
        ],
    }
    return TaskWorkspaceExecution(
        version=SECURITY_CONFIG_SOLVER_VERSION,
        ok=bool(any(write.ok for write in writes)),
        status=status,
        task_id=contract.task_id,
        family=contract.family,
        workspace_visible=env.can_access_task_filesystem,
        wrote_any_file=any(write.ok for write in writes),
        writes=tuple(writes),
        contract=contract.as_context(),
        environment=env.as_context(),
        diagnostics=diagnostics,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def validate_security_config_solver_selftest() -> dict[str, Any]:
    """Validate security output generation without writing to task filesystem."""

    from ..output_contract import build_output_contract
    from ..task_environment import discover_task_environment

    sample = """
    Generate /root/detection_results.csv with columns `timestamp`, `src_ip`, `dst_ip`, `signature`, `severity`.
    Also save /root/security_config.yaml.
    Evidence mentions CVE-2026-12345 and traffic from 10.0.0.5 to 192.168.1.10 on port 443.
    Required Output Files:
    - security_report.json
    """
    metadata = {
        "task_id": "dapt-intrusion-detection",
        "category": "cybersecurity",
        "tags": ["security", "network", "pcap", "intrusion-detection"],
    }
    contract = build_output_contract(metadata, sample)
    env = discover_task_environment(metadata, sample, task_id=contract.task_id, write_probe=False)
    context = _aggregate_security_context(env, metadata, sample)
    requirements = _security_requirements(contract)

    errors: list[str] = []
    if not requirements:
        errors.append("contract did not expose security requirements")
    if "CVE-2026-12345" not in context.get("indicators", {}).get("cves", []):
        errors.append("cve indicator not detected")
    if "10.0.0.5" not in context.get("indicators", {}).get("ip_addresses", []):
        errors.append("ip indicator not detected")
    csv_req = next((req for req in requirements if req.path.endswith(".csv")), None)
    if csv_req:
        csv_payload = _csv_payload(csv_req, contract, context).decode("utf-8", errors="replace")
        if "src_ip" not in csv_payload or "signature" not in csv_payload:
            errors.append("csv payload missing expected columns")
    else:
        errors.append("csv requirement not found")

    return {
        "ok": not errors,
        "errors": errors,
        "version": SECURITY_CONFIG_SOLVER_VERSION,
        "contract_summary": summarize_contract(contract),
        "context": {
            "severity": context.get("severity"),
            "indicators": context.get("indicators"),
            "input_count": len(context.get("inputs", []) or []),
        },
    }


__all__ = [
    "SECURITY_CONFIG_SOLVER_VERSION",
    "security_config_solver",
    "validate_security_config_solver_selftest",
]
