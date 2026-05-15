from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import EvidenceSource, build_evidence_bundle, verify_claims


def test_evidence_gate_supports_claim_with_source_overlap():
    claim = "The tool result confirms the invoice total is 42."
    source = EvidenceSource.new(
        source_type="tool_result",
        title="invoice_tool",
        content="Tool result confirms the invoice total is 42 and the record is present.",
        confidence=0.9,
        refs=("tool.invoice",),
    )

    assessments = verify_claims(claims=[claim], sources=[source])
    assert assessments[0].status == "supported"
    assert assessments[0].confidence > 0.4


def test_evidence_bundle_marks_required_claim_without_sources_as_unsupported():
    bundle = build_evidence_bundle(
        claims=[{"text": "Required evidence must be available before finalization.", "required": True}],
        sources=[],
    )

    assert bundle.assessments[0].status in {"unsupported", "unknown"}
    assert bundle.grounding_decision == "verify_more"
    assert bundle.unsupported_required_count >= 1
