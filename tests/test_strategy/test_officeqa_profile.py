from pathlib import Path


def test_officeqa_profile_exists():
    path = Path("src/aegisforge/strategy/track_profiles/officeqa.toml")
    assert path.exists()
    assert 'track = "officeqa"' in path.read_text(encoding="utf-8")
