"""Tests for CLI commands and exit codes in ethermig.cli."""

import os
from pathlib import Path
import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from ethermig.cli import app
from ethermig.config import load_config
from ethermig.core import get_script_directory, stamp_database

runner = CliRunner()


def test_cli_setup_file(tmp_path: Path, monkeypatch):
    """Verify 'ethermig setup --file' command."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--file"])
    assert result.exit_code == 0
    assert "Created default configuration file" in result.output
    assert (tmp_path / "ethermig.ini").is_file()

    # Second invocation does not overwrite
    result2 = runner.invoke(app, ["setup", "--file"])
    assert result2.exit_code == 0
    assert "already exists" in result2.output


def test_cli_setup_missing_config(tmp_path: Path, monkeypatch):
    """Verify 'ethermig setup' exits 1 when ethermig.ini is missing."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 1
    assert "not found in current directory" in result.output
    # Ensure no traceback is printed
    assert "Traceback" not in result.output


def test_cli_setup_missing_alembic(tmp_path: Path, monkeypatch):
    """Verify 'ethermig setup' exits 1 when alembic.ini is missing."""
    monkeypatch.chdir(tmp_path)
    ini_file = tmp_path / "ethermig.ini"
    ini_file.write_text(
        """[ethermig]
alembic_config = missing_alembic.ini
models_output = models_generated.py

[environments]
db_local = sqlite:///test.db
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 1
    assert "Alembic configuration NOT found" in result.output


def test_cli_setup_success(temp_project: Path, monkeypatch):
    """Verify 'ethermig setup' displays valid table and exits 0 when all DBs connect."""
    monkeypatch.chdir(temp_project)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert "Configuration valid" in result.output
    assert "Alembic configuration found" in result.output
    assert "db_local" in result.output
    assert "Connected" in result.output


def test_cli_setup_verify(temp_project: Path, monkeypatch):
    """Verify 'ethermig setup verify --env db_local' reverse-engineers the database."""
    monkeypatch.chdir(temp_project)

    # Populate db_local with a table
    local_db = temp_project / "local.db"
    engine = sa.create_engine(f"sqlite:///{local_db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE products (id INTEGER PRIMARY KEY, title VARCHAR(100))"))

    result = runner.invoke(app, ["setup", "verify", "--env", "db_local"])
    assert result.exit_code == 0
    assert "Reverse-engineered 1 table(s)" in result.output

    out_file = temp_project / "models_generated.py"
    assert out_file.is_file()
    assert "class Products(SQLModel, table=True):" in out_file.read_text(encoding="utf-8")


def test_cli_current(temp_project: Path, monkeypatch):
    """Verify 'ethermig current' displays revisions across environments."""
    monkeypatch.chdir(temp_project)
    cfg = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(cfg)
    r1 = script_dir.generate_revision("r1", "init schema", refresh=True)
    stamp_database(cfg, env_name="db_local", revision="r1")

    result = runner.invoke(app, ["current"])
    assert result.exit_code == 0
    assert "Migration status" in result.output
    assert "db_local" in result.output
    assert "r1" in result.output
    assert "init schema" in result.output


def test_cli_generate_blocked_by_sync_guard(temp_project: Path, monkeypatch):
    """Verify 'ethermig generate' is blocked when a remote environment contains unapplied migrations."""
    monkeypatch.chdir(temp_project)
    cfg = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(cfg)

    r1 = script_dir.generate_revision("r1", "first", refresh=True)
    r2 = script_dir.generate_revision("r2", "second", head="r1", refresh=True)

    # db_dev is ahead at r2, while db_local is only at r1
    stamp_database(cfg, env_name="db_local", revision="r1")
    stamp_database(cfg, env_name="db_dev", revision="r2")
    stamp_database(cfg, env_name="db_prod", revision="r1")

    result = runner.invoke(app, ["generate", "-m", "new feature", "--env", "db_local"])
    assert result.exit_code == 1
    assert "Migration generation blocked" in result.output
    assert "db_dev contains unapplied migration(s):" in result.output
    assert "r2" in result.output
    assert "Traceback" not in result.output


def test_cli_generate_success(temp_project: Path, monkeypatch):
    """Verify 'ethermig generate' generates migration when environments are synchronized."""
    monkeypatch.chdir(temp_project)
    cfg = load_config(temp_project / "ethermig.ini")

    # Add a table to db_local
    local_db = temp_project / "local.db"
    engine = sa.create_engine(f"sqlite:///{local_db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)"))

    # All environments start empty (<base>) so reference is in sync
    result = runner.invoke(app, ["generate", "-m", "add widgets", "--env", "db_local"])
    assert result.exit_code == 0
    assert "Migration generated successfully!" in result.output
    assert "Revision:" in result.output


def test_cli_upgrade_downgrade_stamp(temp_project: Path, monkeypatch):
    """Verify upgrade, downgrade, and stamp CLI commands."""
    monkeypatch.chdir(temp_project)
    cfg = load_config(temp_project / "ethermig.ini")
    script_dir = get_script_directory(cfg)
    r1 = script_dir.generate_revision("r1", "first migration", refresh=True)

    # Upgrade
    res_up = runner.invoke(app, ["upgrade", "head", "--env", "db_local"])
    assert res_up.exit_code == 0
    assert "Upgraded db_local to head" in res_up.output

    # Downgrade
    res_down = runner.invoke(app, ["downgrade", "base", "--env", "db_local"])
    assert res_down.exit_code == 0
    assert "Downgraded db_local to base" in res_down.output

    # Stamp
    res_stamp = runner.invoke(app, ["stamp", "r1", "--env", "db_local"])
    assert res_stamp.exit_code == 0
    assert "Stamped db_local with r1" in res_stamp.output
