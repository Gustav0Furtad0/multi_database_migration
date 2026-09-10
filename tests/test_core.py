"""Tests for core migration management, Alembic integration, and sync guard in ethermig.core."""

from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from ethermig.config import EthermigConfig, load_config
from ethermig.core import (
    check_migration_sync,
    downgrade_database,
    generate_migration,
    get_ancestor_revisions,
    get_current_heads,
    get_engine,
    get_environment_status,
    get_script_directory,
    stamp_database,
    test_connection as core_test_connection,
    upgrade_database,
)
from ethermig.exceptions import (
    DatabaseConnectionError,
    MigrationSyncError,
)


def test_test_connection(tmp_path: Path):
    """Verify testing database connection succeeds for valid DB and fails gracefully for invalid."""
    db_path = tmp_path / "valid.db"
    valid_url = f"sqlite:///{db_path}"
    ok, err = core_test_connection(valid_url)
    assert ok is True
    assert err is None

    # Invalid connection URL (unreachable host/port)
    invalid_url = "postgresql://user:pass@127.0.0.1:59999/nonexistent"
    bad_ok, bad_err = core_test_connection(invalid_url, timeout=1)
    assert bad_ok is False
    assert bad_err is not None
    assert "pass" not in bad_err


def test_get_current_heads(tmp_path: Path, temp_project: Path):
    """Verify reading migration heads via MigrationContext."""
    config = load_config(temp_project / "ethermig.ini")
    engine = get_engine(config.get_database_url("db_local"))

    # Initially empty, no alembic_version table
    heads = get_current_heads(engine)
    assert heads == ()

    # Stamp with dummy head after generating a revision
    script_dir = get_script_directory(config)
    rev = script_dir.generate_revision("rev_1", "initial", refresh=True)
    stamp_database(config, env_name="db_local", revision=rev.revision)

    heads = get_current_heads(engine)
    assert heads == (rev.revision,)


def test_get_ancestor_revisions_linear_and_branches(temp_project: Path):
    """Verify ancestor graph traversal for linear, branched, and merge revisions."""
    config = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(config)

    # rev1
    r1 = script_dir.generate_revision("r1", "rev 1", refresh=True)
    # rev2a branching from rev1
    r2a = script_dir.generate_revision("r2a", "branch a", head="r1", refresh=True)
    # rev2b branching from rev1
    r2b = script_dir.generate_revision("r2b", "branch b", head="r1", splice=True, refresh=True)
    # rev3 merging rev2a and rev2b
    r3 = script_dir.generate_revision("r3", "merge", head=["r2a", "r2b"], refresh=True)

    # Ancestors of r1: {r1}
    assert get_ancestor_revisions(script_dir, ["r1"]) == {"r1"}
    # Ancestors of r2a: {r1, r2a}
    assert get_ancestor_revisions(script_dir, ["r2a"]) == {"r1", "r2a"}
    # Ancestors of r2b: {r1, r2b}
    assert get_ancestor_revisions(script_dir, ["r2b"]) == {"r1", "r2b"}
    # Ancestors of r3 (merge): {r1, r2a, r2b, r3}
    assert get_ancestor_revisions(script_dir, ["r3"]) == {"r1", "r2a", "r2b", "r3"}


def test_sync_guard_safe_state(temp_project: Path):
    """Verify check_migration_sync passes when reference environment is at or ahead."""
    config = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(config)

    r1 = script_dir.generate_revision("r1", "rev 1", refresh=True)
    r2 = script_dir.generate_revision("r2", "rev 2", head="r1", refresh=True)

    # db_local is at r2
    stamp_database(config, env_name="db_local", revision="r2")
    # db_dev and db_prod are at r1
    stamp_database(config, env_name="db_dev", revision="r1")
    stamp_database(config, env_name="db_prod", revision="r1")

    res = check_migration_sync(config, reference_env="db_local")
    assert res.is_synced is True
    assert len(res.unapplied_by_env) == 0
    assert len(res.unreachable_envs) == 0


def test_sync_guard_unsafe_state_blocked(temp_project: Path):
    """Verify check_migration_sync detects remote unapplied revisions."""
    config = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(config)

    r1 = script_dir.generate_revision("r1", "rev 1", refresh=True)
    r2 = script_dir.generate_revision("r2", "rev 2", head="r1", refresh=True)

    # db_local is at r1, but db_dev is ahead at r2
    stamp_database(config, env_name="db_local", revision="r1")
    stamp_database(config, env_name="db_dev", revision="r2")
    stamp_database(config, env_name="db_prod", revision="r1")

    res = check_migration_sync(config, reference_env="db_local")
    assert res.is_synced is False
    assert "db_dev" in res.unapplied_by_env
    assert "r2" in res.unapplied_by_env["db_dev"]

    # generate_migration must fail closed with MigrationSyncError
    with pytest.raises(MigrationSyncError) as exc_info:
        generate_migration(config, message="test conflict", reference_env="db_local")

    assert "db_dev" in exc_info.value.unapplied_by_env
    assert "r2" in exc_info.value.unapplied_by_env["db_dev"]


def test_sync_guard_unreachable_fail_closed(temp_project: Path):
    """Verify check_migration_sync fails closed when a remote database cannot be reached."""
    # Add an unreachable environment to ethermig.ini
    ini_path = temp_project / "ethermig.ini"
    ini_path.write_text(
        f"""[ethermig]
alembic_config = alembic.ini
models_output = models_generated.py

[environments]
db_local = sqlite:///{temp_project.as_posix()}/local.db
db_unreachable = postgresql://user:pass@127.0.0.1:59998/offline
""",
        encoding="utf-8",
    )

    config = load_config(ini_path)
    res = check_migration_sync(config, reference_env="db_local")
    assert res.is_synced is False
    assert "db_unreachable" in res.unreachable_envs

    with pytest.raises(MigrationSyncError) as exc_info:
        generate_migration(config, message="should fail", reference_env="db_local")
    assert "db_unreachable" in exc_info.value.unreachable_envs


def test_migration_lifecycle(temp_project: Path):
    """Verify upgrade, downgrade, and stamp lifecycle across an environment."""
    config = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(config)

    # Generate revision 1
    r1 = script_dir.generate_revision("r1", "first", refresh=True)
    # Generate revision 2
    r2 = script_dir.generate_revision("r2", "second", head="r1", refresh=True)

    # Upgrade to head
    upgrade_database(config, env_name="db_local", revision="head")
    engine = get_engine(config.get_database_url("db_local"))
    assert get_current_heads(engine) == ("r2",)

    # Downgrade by 1
    downgrade_database(config, env_name="db_local", revision="-1")
    assert get_current_heads(engine) == ("r1",)

    # Stamp to base
    stamp_database(config, env_name="db_local", revision="base")
    assert get_current_heads(engine) == ()


def test_get_environment_status(temp_project: Path):
    """Verify inspecting environment statuses across multiple databases."""
    config = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(config)
    r1 = script_dir.generate_revision("r1", "my migration message", refresh=True)
    stamp_database(config, env_name="db_local", revision="r1")

    statuses = get_environment_status(config)
    assert len(statuses) == 3

    local_st = next(s for s in statuses if s.name == "db_local")
    assert local_st.is_connected is True
    assert len(local_st.revisions) == 1
    assert local_st.revisions[0].revision == "r1"
    assert local_st.revisions[0].message == "my migration message"
