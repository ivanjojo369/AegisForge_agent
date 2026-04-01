from pathlib import Path


def test_crmarena_profile_has_security_guards():
    content = Path("src/aegisforge/strategy/track_profiles/crmarena.toml").read_text(encoding="utf-8")
    assert 'deny_formula_disclosure = true' in content
    assert 'deny_prompt_disclosure = true' in content
