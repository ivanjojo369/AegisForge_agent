from src.aegisforge.orchestration.state_reset import StateResetManager

def test_state_reset_manager_emits_ready_report():
    manager = StateResetManager()
    report = manager.build_reset_report()
    assert report["status"] == "ready"
    assert len(report["actions"]) >= 1
