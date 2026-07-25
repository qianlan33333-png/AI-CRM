from __future__ import annotations

from pathlib import Path

from tools.check_runtime_configuration_contract import check_runtime_configuration_contract


ROOT = Path(__file__).resolve().parents[1]


def test_migrated_contexts_satisfy_runtime_configuration_contract() -> None:
    assert check_runtime_configuration_contract(root=ROOT) == []


def test_direct_environment_access_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text('import os\nVALUE = os.getenv("AICRM_EXAMPLE")\n', encoding="utf-8")

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys={"AICRM_EXAMPLE"},
    )

    assert {item.rule for item in violations} == {"direct_environment_access_forbidden"}


def test_runtime_setting_requires_config_definition(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        'from aicrm_next.shared.runtime_settings import runtime_setting\nVALUE = runtime_setting("AICRM_UNKNOWN")\n',
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys=set(),
    )

    assert [item.rule for item in violations] == ["runtime_setting_missing_definition"]


def test_dynamic_runtime_setting_requires_declared_key_catalog(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from aicrm_next.shared.runtime_settings import runtime_bool\n"
        "def enabled(key: str) -> bool:\n"
        "    return runtime_bool(key)\n",
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys=set(),
    )

    assert [item.rule for item in violations] == ["dynamic_runtime_setting_without_declaration"]


def test_managed_runtime_setting_requires_cutover_registration(tmp_path: Path) -> None:
    path = tmp_path / "aicrm_next" / "example.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from aicrm_next.shared.runtime_settings import managed_runtime_setting\n"
        'VALUE = managed_runtime_setting("AICRM_UNREGISTERED")\n',
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=("aicrm_next/example.py",),
        definition_keys={"AICRM_UNREGISTERED"},
    )

    assert [item.rule for item in violations] == [
        "managed_runtime_setting_missing_cutover_registration"
    ]


def test_cutover_key_direct_environment_access_is_rejected_globally(
    tmp_path: Path,
) -> None:
    path = tmp_path / "aicrm_next" / "outside_migrated_context.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        'import os\nVALUE = os.getenv("WECOM_CORP_ID")\n',
        encoding="utf-8",
    )

    violations = check_runtime_configuration_contract(
        root=tmp_path,
        migrated_paths=(),
        definition_keys={"WECOM_CORP_ID"},
    )

    assert [item.rule for item in violations] == [
        "cutover_key_direct_environment_access"
    ]
