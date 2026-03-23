from src.aegisforge_eval.heldouts.registry import HeldoutCase, HeldoutRegistry

def test_registry_registers_and_filters_cases():
    registry = HeldoutRegistry()
    registry.register(HeldoutCase(case_id="c1", suite="smoke", prompt="hello"))
    registry.register(HeldoutCase(case_id="c2", suite="budget", prompt="world"))

    assert registry.get("c1") is not None
    assert len(registry.by_suite("smoke")) == 1
    assert len(registry.all_cases()) == 2
