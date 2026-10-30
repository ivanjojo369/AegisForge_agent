"""Unified regression suite for three historical dashboard asset repair tools.

Merges (without dropping tests):
* test_dashboard_asset_recovery.py (12 checks)
* test_dashboard_recovery_v3.py (10 checks)
* test_repair_mission_dashboard_assets.py (7 checks)

All work is limited to pytest temporary directories. No live HTTP, Nebius,
repository edits, or network access. Auxiliary scripts are imported LAZILY:
missing scripts produce a readable test failure, never a pytest collection error.

Install this file at tests/test_nexus/test_dashboard_assets_recovery_unified.py.
The original three test files must be removed after verifying this replacement.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import pytest

# tests/test_nexus/ -> tests/ -> repository root
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

class _ScriptProxy:
    """Load the real installed maintenance script on first use, not at import."""
    def __init__(self, filename: str, module_name: str):
        self.filename = filename
        self.module_name = module_name
        self._module = None

    def __getattr__(self, key: str):
        if self._module is None:
            source = REPOSITORY_ROOT / 'scripts' / 'nexus' / self.filename
            if not source.is_file():
                pytest.fail(
                    f'Missing required test subject: {source}. '
                    'Restore its original script to scripts/nexus/; '
                    'do not change mission_http_api.py or dashboard assets.',
                    pytrace=False,
                )
            spec = importlib.util.spec_from_file_location(self.module_name, source)
            if spec is None or spec.loader is None:
                pytest.fail(f'Cannot import support script: {source}', pytrace=False)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._module = module
        return getattr(self._module, key)

recover_v2 = _ScriptProxy('diagnose_repair_dashboard_assets.py', 'asset_recovery_v2_combined')
recover_v3 = _ScriptProxy('repair_dashboard_assets_standalone.py', 'dashboard_recovery_v3_combined')
patch_v1 = _ScriptProxy('repair_mission_dashboard_assets.py', 'colossus_assets_repair_combined')

@pytest.fixture(scope='module')
def v2_payload(tmp_path_factory):
    """Get actual expected assets from the v3 bundle; no repair_payload folder."""
    payload_dir = tmp_path_factory.mktemp('dashboard_v2_payload')
    for name in recover_v3.ASSETS:
        (payload_dir / name).write_bytes(recover_v3.extract(name))
    return payload_dir


# === Historical v2 diagnostic and repair (12 tests) ===
@pytest.fixture
def v2_project(tmp_path):
    api = tmp_path / recover_v2.API
    api.parent.mkdir(parents=True, exist_ok=True)
    api.write_text('from pathlib import Path\n' + recover_v2.OLD_ASSIGN + '\n'
                   + "FILES=['mission_dashboard.html', 'mission_dashboard.css', 'mission_dashboard.js']\n", encoding='utf-8')
    return tmp_path


def test_v2_dry_run_does_not_write(v2_project, v2_payload):
    old = (v2_project / recover_v2.API).read_bytes()
    report = recover_v2.run(v2_project, v2_payload, check_import=False)
    assert report['needs_api_patch'] and len(report['assets_to_restore']) == 3
    assert report['completed'] is False
    assert (v2_project / recover_v2.API).read_bytes() == old
    assert not (v2_project / recover_v2.DEST).exists()


def test_v2_apply_restores_three_assets_and_changes_root(v2_project, v2_payload):
    result = recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    assert result['completed']
    assert Path(result['backup_dir']).is_dir()
    for n in recover_v2.NAMES:
        assert (v2_project / recover_v2.DEST / n).read_bytes() == (v2_payload / n).read_bytes()
    assert recover_v2.NEW_ASSIGN in (v2_project / recover_v2.API).read_text()
    assert recover_v2.OLD_ASSIGN not in (v2_project / recover_v2.API).read_text()


def test_v2_rerun_is_noop_and_preserves_files(v2_project, v2_payload):
    recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    before = {n: recover_v2.digest(v2_project / recover_v2.DEST / n) for n in recover_v2.NAMES}
    result = recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    assert result['completed'] and result['backup_dir'] is None
    assert {n: recover_v2.digest(v2_project / recover_v2.DEST / n) for n in recover_v2.NAMES} == before


def test_v2_legacy_assets_take_priority_over_bundled_payload(v2_project, v2_payload):
    old = v2_project / recover_v2.OLD_DIR
    old.mkdir(parents=True)
    for name in recover_v2.NAMES:
        shutil.copy2(v2_payload / name, old / name)
    # Safe, recognizable modified asset should be preserved during migration.
    (old / 'mission_dashboard.css').write_text((old / 'mission_dashboard.css').read_text() + '\n/* legacy custom */\n')
    recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    assert 'legacy custom' in (v2_project / recover_v2.DEST / 'mission_dashboard.css').read_text()


def test_v2_preserves_valid_existing_customized_asset(v2_project, v2_payload):
    dest = v2_project / recover_v2.DEST
    dest.mkdir(parents=True)
    for name in recover_v2.NAMES:
        shutil.copy2(v2_payload / name, dest / name)
    (dest / 'mission_dashboard.js').write_text((dest / 'mission_dashboard.js').read_text() + '\n// customized\n')
    recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    assert '// customized' in (dest / 'mission_dashboard.js').read_text()


def test_v2_replaces_broken_target_with_backup(v2_project, v2_payload):
    dest = v2_project / recover_v2.DEST
    dest.mkdir(parents=True)
    (dest / 'mission_dashboard.js').write_text('oops')
    with pytest.raises(recover_v2.RecoveryError, match='unrecognized existing assets preserved'):
        recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    report = recover_v2.run(v2_project, v2_payload, apply=True, check_import=False, replace_invalid=True)
    assert (Path(report['backup_dir']) / 'mission_dashboard.js').read_text() == 'oops'
    assert 'Bearer ${bearer}' in (dest / 'mission_dashboard.js').read_text()


def test_v2_unknown_api_layout_refuses_changes(v2_project, v2_payload):
    api = v2_project / recover_v2.API
    api.write_text(api.read_text().replace(recover_v2.OLD_ASSIGN, 'assets_root = Path("/unsafe")'))
    with pytest.raises(recover_v2.RecoveryError, match='unknown'):
        recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    assert not (v2_project / recover_v2.DEST).exists()


def test_v2_missing_payload_refuses_writes(v2_project, tmp_path):
    with pytest.raises(recover_v2.RecoveryError, match='no valid recovery source'):
        recover_v2.run(v2_project, tmp_path / 'missing', apply=True, check_import=False)
    assert recover_v2.OLD_ASSIGN in (v2_project / recover_v2.API).read_text()


def test_v2_new_assignment_with_missing_assets_repairs(v2_project, v2_payload):
    api = v2_project / recover_v2.API
    api.write_text(api.read_text().replace(recover_v2.OLD_ASSIGN, recover_v2.NEW_ASSIGN))
    result = recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)
    assert result['completed'] and not result['needs_api_patch']
    assert all((v2_project / recover_v2.DEST / n).is_file() for n in recover_v2.NAMES)


def test_v2_symlink_source_rejected(v2_project, v2_payload):
    dest = v2_project / recover_v2.DEST
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.symlink_to(v2_project / recover_v2.OLD_DIR, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlink privilege unavailable')
    with pytest.raises(recover_v2.RecoveryError, match='symbolic'):
        recover_v2.run(v2_project, v2_payload, apply=True, check_import=False)


def test_v2_different_import_path_refuses_write(v2_project, monkeypatch, v2_payload):
    _ = recover_v2.RecoveryError  # load the real module before patching globals
    monkeypatch.setattr(recover_v2._module, '_module_origin', lambda: '/another/site-packages/mission_http_api.py')
    with pytest.raises(recover_v2.RecoveryError, match='imported another'):
        recover_v2.run(v2_project, v2_payload, apply=True)
    assert recover_v2.OLD_ASSIGN in (v2_project / recover_v2.API).read_text()


def test_v2_bundled_assets_are_valid(v2_payload):
    assert all(recover_v2.valid_asset(v2_payload / n)[0] for n in recover_v2.NAMES)


# === Embedded-assets v3 repair (10 tests) ===
@pytest.fixture
def v3_root(tmp_path):
    api = tmp_path / recover_v3.API
    api.parent.mkdir(parents=True)
    api.write_text('from pathlib import Path\n' +
                   '# reference: mission_dashboard.html / mission_dashboard.css / mission_dashboard.js\n' +
                   'def static_assets():\n' + '    ' + recover_v3.OLD_ASSIGN + '\n', encoding='utf8')
    return tmp_path


def test_v3_embedded_content_matches_uploaded_sources():
    for name in recover_v3.ASSETS:
        content = recover_v3.extract(name)
        assert len(content) > 1000
        assert recover_v3.digest(content) == recover_v3.EXPECTED_SHA256[name]


def test_v3_dry_run_never_writes(v3_root):
    before = (v3_root / recover_v3.API).read_bytes()
    result = recover_v3.run(v3_root, check_import=False)
    assert result['completed'] is False
    assert result['needs_api_patch'] is True
    assert len(result['assets_to_restore']) == 3
    assert (v3_root / recover_v3.API).read_bytes() == before
    assert not (v3_root / recover_v3.DEST).exists()


def test_v3_repair_from_embedded_resources(v3_root):
    result = recover_v3.run(v3_root, apply=True, check_import=False)
    assert result['completed'] is True
    assert result['backup_dir']
    assert recover_v3.NEW_ASSIGN in (v3_root / recover_v3.API).read_text('utf8')
    for name in recover_v3.ASSETS:
        assert (v3_root / recover_v3.DEST / name).read_bytes() == recover_v3.extract(name)
    report = recover_v3.inspect(v3_root, check_import=False)
    assert not report['assets_to_restore']
    assert not report['needs_api_patch']


def test_v3_second_apply_is_idempotent(v3_root):
    recover_v3.run(v3_root, apply=True, check_import=False)
    snap = {(v3_root / recover_v3.DEST / name).read_bytes() for name in recover_v3.ASSETS}
    result = recover_v3.run(v3_root, apply=True, check_import=False)
    assert result['completed'] is True
    assert result['backup_dir'] is None
    assert snap == {(v3_root / recover_v3.DEST / name).read_bytes() for name in recover_v3.ASSETS}


def test_v3_custom_asset_never_overwritten_without_flag(v3_root):
    custom = v3_root / recover_v3.DEST / 'mission_dashboard.html'
    custom.parent.mkdir(parents=True)
    custom.write_text('MY CUSTOM UI', 'utf8')
    with pytest.raises(recover_v3.DashboardRecoveryError, match='custom/incompatible'):
        recover_v3.run(v3_root, apply=True, check_import=False)
    assert custom.read_text('utf8') == 'MY CUSTOM UI'
    assert recover_v3.OLD_ASSIGN in (v3_root / recover_v3.API).read_text('utf8')


def test_v3_opt_in_replaces_custom_asset_and_backups(v3_root):
    custom = v3_root / recover_v3.DEST / 'mission_dashboard.html'
    custom.parent.mkdir(parents=True)
    custom.write_text('MY CUSTOM UI', 'utf8')
    report = recover_v3.run(v3_root, apply=True, replace_invalid=True, check_import=False)
    assert report['completed'] is True
    assert (Path(report['backup_dir']) / 'mission_dashboard.html').read_text('utf8') == 'MY CUSTOM UI'
    assert custom.read_bytes() == recover_v3.extract('mission_dashboard.html')


def test_v3_refuses_unknown_api_assignment(v3_root):
    api = v3_root / recover_v3.API
    api.write_text(api.read_text('utf8').replace(recover_v3.OLD_ASSIGN,'assets_root = Path("unrecognized")'), 'utf8')
    with pytest.raises(recover_v3.DashboardRecoveryError, match='customized'):
        recover_v3.run(v3_root, apply=True, check_import=False)


def test_v3_refuses_symlink_destination(v3_root):
    link = v3_root / recover_v3.DEST
    link.parent.mkdir(parents=True)
    other = v3_root / 'outside'
    other.mkdir()
    try:
        link.symlink_to(other, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip('symlinks unavailable')
    with pytest.raises(recover_v3.DashboardRecoveryError, match='symlink'):
        recover_v3.run(v3_root, apply=True, check_import=False)


def test_v3_existing_valid_new_assets_only_patches_route(v3_root):
    dest = v3_root / recover_v3.DEST
    dest.mkdir(parents=True)
    for name in recover_v3.ASSETS:
        (dest / name).write_bytes(recover_v3.extract(name))
    result = recover_v3.run(v3_root, apply=True, check_import=False)
    assert result['completed'] is True
    assert recover_v3.NEW_ASSIGN in (v3_root / recover_v3.API).read_text('utf8')


def test_v3_recognized_old_assets_can_be_migrated(v3_root):
    old = v3_root / recover_v3.OLD
    old.mkdir(parents=True)
    for name in recover_v3.ASSETS:
        (old / name).write_bytes(recover_v3.extract(name))
    report = recover_v3.run(v3_root, apply=True, check_import=False)
    assert report['completed']
    for name in recover_v3.ASSETS:
        assert (v3_root / recover_v3.DEST / name).read_bytes() == (old / name).read_bytes()
        assert (old / name).is_file()


# === Original path-only migration patch (7 tests) ===
def make_patch_fixture(root: Path, assignment=None):
    api = root / patch_v1.API_REL
    assets = root / patch_v1.ASSETS_REL
    api.parent.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    for name in patch_v1.ASSETS:
        (assets / name).write_text(name, encoding="utf-8")
    code = "from pathlib import Path\n" + "assets_root = " + (assignment or 'Path(__file__).parent / "web"') + "\n" + "\n".join('# '+p for p in patch_v1.ASSETS) + "\n"
    api.write_text(code, encoding="utf-8")
    return api


def test_patch_check_is_read_only(tmp_path):
    api = make_patch_fixture(tmp_path)
    original = api.read_bytes()
    result = patch_v1.repair(tmp_path)
    assert result['needs_patch'] is True and result['completed'] is False
    assert api.read_bytes() == original
    assert not list(api.parent.glob('*.backup-*'))


def test_patch_apply_and_backup_with_same_hash(tmp_path):
    api = make_patch_fixture(tmp_path)
    original = api.read_bytes()
    result = patch_v1.repair(tmp_path, apply=True)
    assert result['completed'] is True
    assert patch_v1.NEW in api.read_text(encoding='utf-8')
    assert Path(result['backup']).read_bytes() == original


def test_patch_repeat_is_idempotent(tmp_path):
    api = make_patch_fixture(tmp_path)
    patch_v1.repair(tmp_path, apply=True)
    after = api.read_bytes()
    result = patch_v1.repair(tmp_path, apply=True)
    assert result['completed'] is True and result['needs_patch'] is False
    assert result['backup'] is None and api.read_bytes() == after


def test_patch_missing_assets_never_patch(tmp_path):
    api = make_patch_fixture(tmp_path)
    original = api.read_bytes()
    (tmp_path / patch_v1.ASSETS_REL / 'mission_dashboard.js').unlink()
    with pytest.raises(patch_v1.RepairError, match='missing migrated assets'):
        patch_v1.repair(tmp_path, apply=True)
    assert api.read_bytes() == original


def test_patch_unknown_api_layout_refused(tmp_path):
    api = make_patch_fixture(tmp_path, assignment='Path("some-other-location")')
    original = api.read_bytes()
    with pytest.raises(patch_v1.RepairError, match='unknown assets_root'):
        patch_v1.repair(tmp_path, apply=True)
    assert api.read_bytes() == original


def test_patch_symlink_asset_refused(tmp_path):
    api = make_patch_fixture(tmp_path)
    linked = tmp_path / patch_v1.ASSETS_REL / 'mission_dashboard.js'
    linked.unlink()
    try:
        linked.symlink_to(tmp_path / patch_v1.ASSETS_REL / 'mission_dashboard.html')
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    with pytest.raises(patch_v1.RepairError, match='symlink'):
        patch_v1.repair(tmp_path, apply=True)


def test_patch_absolute_root_and_crlf_preserved(tmp_path):
    api = make_patch_fixture(tmp_path)
    api.write_bytes(api.read_bytes().replace(b'\n', b'\r\n'))
    patch_v1.repair(tmp_path, apply=True)
    assert b'\r\n' in api.read_bytes()
    assert b'\n' not in api.read_bytes().replace(b'\r\n',b'')
