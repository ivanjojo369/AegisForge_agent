from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import AttentionGate, WorkingMemoryStore, format_memory_prompt


def test_working_memory_builds_from_attention_frame_and_queries():
    frame = AttentionGate().select(
        task_text="Do not hardcode answers. Verify evidence before using the web tool.",
        metadata={"domain": "web", "scenario_id": "SearchGlitch"},
        policy_context={"denied_behaviors": ["hardcoded_answers", "secret_extraction"]},
    )
    store = WorkingMemoryStore.from_attention_frame(frame)

    selection = store.query()
    prompt = format_memory_prompt(selection)

    assert len(store) > 0
    assert selection.selected
    assert "NCP Working Memory" in prompt
    assert any(score.item.locked for score in selection.selected)


def test_working_memory_redacts_sensitive_values_and_preserves_locked_items():
    store = WorkingMemoryStore()
    item = store.build_item(
        key="api_key",
        source="policy",
        content="api_key=sk-abcdefghijklmnopqrstuvwxyz",
        salience=0.99,
        locked=True,
        tags=("secret_boundary",),
    )
    store.add(item)
    store.clear(preserve_locked=True)

    assert len(store.items()) == 1
    assert "<redacted>" in store.items()[0].content
    assert store.items()[0].locked is True
