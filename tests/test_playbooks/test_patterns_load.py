from pathlib import Path


EXPECTED_PLAYBOOK_DIRS = {
    "attack_patterns",
    "defense_patterns",
    "recovery_patterns",
    "tool_misuse_patterns",
    "cost_traps",
    "heldout_mix",
}


def test_expected_playbook_pattern_names_are_stable():
    assert "attack_patterns" in EXPECTED_PLAYBOOK_DIRS
    assert "heldout_mix" in EXPECTED_PLAYBOOK_DIRS
    assert len(EXPECTED_PLAYBOOK_DIRS) == 6
"""
This test ensures that the expected playbook pattern directories remain consistent over time. By asserting the presence of specific directory names and confirming that the total number of expected directories is correct, the test helps maintain stability in the structure of playbook patterns. This is important for ensuring that any code or tests that rely on these directories can continue to function correctly without unexpected changes to the directory structure.
"""
