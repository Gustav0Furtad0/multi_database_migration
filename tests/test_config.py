"""Tests for configuration handling in ethermig.config."""

from pathlib import Path
import pytest

from ethermig.config import (
    EthermigConfig,
    create_default_config,
    find_config_file,
    load_config,
    mask_url,
)
from ethermig.exceptions import ConfigurationError


def test_mask_url():
    """Verify password masking in database URLs."""
    # Standard PostgreSQL URL with password
    pg_url = "postgresql://my_user:secret_pass123@db.example.com:5432/my_database"
    masked = mask_url(pg_url)
    assert "secret_pass123" not in masked
    assert "my_user" in masked
    assert "db.example.com" in masked
    assert "***" in masked

    # SQLite URL without credentials
    sqlite_url = "sqlite:///relative/path.db"
    assert mask_url(sqlite_url) == sqlite_url

    # Non-standard or raw fallback URL
    raw_url = "custom://admin:topsecret@host:9999/data"
    masked_raw = mask_url(raw_url)
    assert "topsecret" not in masked_raw
    assert "admin:***@host" in masked_raw

    # Empty string
    assert mask_url("") == ""


def test_load_valid_config(tmp_path: Path):
    """Verify loading and resolving paths in a valid configuration."""
    ini_file = tmp_path / "ethermig.ini"
    ini_file.write_text(
        """[ethermig]
alembic_config = config/alembic.ini
models_output = generated/models.py

[environments]
db_staging = postgresql://user:pass@staging:5432/app
db_local = sqlite:///local.db
""",
        encoding="utf-8",
    )

    cfg = load_config(ini_file)
    assert cfg.config_path == ini_file.resolve()
    assert cfg.alembic_config_path == (tmp_path / "config" / "alembic.ini").resolve()
    assert cfg.models_output_path == (tmp_path / "generated" / "models.py").resolve()
    assert len(cfg.environments) == 2
    assert cfg.get_database_url("db_local") == "sqlite:///local.db"
    assert "pass" not in cfg.get_masked_url("db_staging")


def test_load_config_missing_file(tmp_path: Path):
    """Verify error when config file does not exist."""
    missing = tmp_path / "non_existent.ini"
    with pytest.raises(ConfigurationError, match="Configuration file not found"):
        load_config(missing)


def test_missing_ethermig_section(tmp_path: Path):
    """Verify error when [ethermig] section is missing."""
    ini_file = tmp_path / "ethermig.ini"
    ini_file.write_text(
        """[environments]
db_local = sqlite:///test.db
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Missing required section '\\[ethermig\\]'"):
        load_config(ini_file)


def test_missing_alembic_config_option(tmp_path: Path):
    """Verify error when alembic_config option is missing."""
    ini_file = tmp_path / "ethermig.ini"
    ini_file.write_text(
        """[ethermig]
models_output = models.py

[environments]
db_local = sqlite:///test.db
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Missing 'alembic_config'"):
        load_config(ini_file)


def test_missing_environments_section(tmp_path: Path):
    """Verify error when [environments] section is missing."""
    ini_file = tmp_path / "ethermig.ini"
    ini_file.write_text(
        """[ethermig]
alembic_config = alembic.ini
models_output = models.py
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Missing required section '\\[environments\\]'"):
        load_config(ini_file)


def test_empty_environments(tmp_path: Path):
    """Verify error when no environments are specified in [environments]."""
    ini_file = tmp_path / "ethermig.ini"
    ini_file.write_text(
        """[ethermig]
alembic_config = alembic.ini
models_output = models.py

[environments]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="No environments configured"):
        load_config(ini_file)


def test_get_database_url_unknown_env(tmp_path: Path):
    """Verify error when querying an unconfigured environment."""
    ini_file = tmp_path / "ethermig.ini"
    ini_file.write_text(
        """[ethermig]
alembic_config = alembic.ini
models_output = models.py

[environments]
db_local = sqlite:///test.db
""",
        encoding="utf-8",
    )
    cfg = load_config(ini_file)
    with pytest.raises(ConfigurationError, match="Environment 'non_existent' not found"):
        cfg.get_database_url("non_existent")


def test_create_default_config(tmp_path: Path):
    """Verify creating default configuration without overwriting existing files."""
    # First call creates the file
    created, path = create_default_config(tmp_path)
    assert created is True
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert "[ethermig]" in content
    assert "[environments]" in content
    assert "db_local" in content

    # Second call detects existing file and does not overwrite
    path.write_text("CUSTOM_CONTENT", encoding="utf-8")
    created2, path2 = create_default_config(tmp_path)
    assert created2 is False
    assert path2.read_text(encoding="utf-8") == "CUSTOM_CONTENT"
