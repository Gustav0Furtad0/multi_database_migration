"""Configuration handling for ethermig."""

import configparser
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Optional

from sqlalchemy.engine.url import make_url

from ethermig.exceptions import ConfigurationError

CONFIG_FILENAME = "ethermig.ini"
DEFAULT_ALEMBIC_CONFIG = "alembic.ini"
DEFAULT_MODELS_OUTPUT = "models_generated.py"

DEFAULT_CONFIG_TEMPLATE = f"""[ethermig]
alembic_config = {DEFAULT_ALEMBIC_CONFIG}
models_output = {DEFAULT_MODELS_OUTPUT}

[environments]
db_prod = postgresql://user:pass@prod-host:5432/prod_db
db_dev = postgresql://user:pass@dev-host:5432/dev_db
db_local = postgresql://user:pass@localhost:5432/local_db
"""


def mask_url(url: str) -> str:
    """Mask password credentials in a database connection URL.

    Never exposes credentials in terminal output or logs.
    """
    if not url:
        return ""
    try:
        sa_url = make_url(url)
        return sa_url.render_as_string(hide_password=True)
    except Exception:
        # Fallback regex masking for any non-standard or malformed database URLs
        return re.sub(r":([^/@:]+)@", r":***@", url)


@dataclass(frozen=True)
class EthermigConfig:
    """Validated configuration for ethermig."""

    config_path: Path
    alembic_config_path: Path
    models_output_path: Path
    environments: dict[str, str] = field(default_factory=dict)

    @property
    def project_root(self) -> Path:
        """The directory containing ethermig.ini."""
        return self.config_path.parent

    def get_database_url(self, env_name: str) -> str:
        """Get the database URL for a specific environment."""
        if env_name not in self.environments:
            available = ", ".join(f"'{e}'" for e in sorted(self.environments.keys()))
            raise ConfigurationError(
                f"Environment '{env_name}' not found in configuration. "
                f"Configured environments: {available if available else 'none'}"
            )
        return self.environments[env_name]

    def get_masked_url(self, env_name: str) -> str:
        """Get the password-masked database URL for an environment."""
        return mask_url(self.get_database_url(env_name))

    def validate_alembic_exists(self) -> None:
        """Validate that the configured alembic.ini exists."""
        if not self.alembic_config_path.is_file():
            raise ConfigurationError(
                f"Alembic configuration file not found at: {self.alembic_config_path}"
            )


def find_config_file(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Search for ethermig.ini starting from start_dir and traversing parent directories."""
    current = (start_dir or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Optional[Path] = None) -> EthermigConfig:
    """Load and validate ethermig.ini.

    Args:
        path: Explicit path to ethermig.ini. If None, searches current directory and parents.

    Raises:
        ConfigurationError: If file does not exist, syntax is invalid, or required sections are missing.
    """
    if path is not None:
        config_path = Path(path).resolve()
        if not config_path.is_file():
            raise ConfigurationError(f"Configuration file not found: {config_path}")
    else:
        found = find_config_file()
        if not found:
            raise ConfigurationError(
                f"'{CONFIG_FILENAME}' not found in current directory or any parent directory. "
                f"Run 'ethermig setup --file' to create a default configuration."
            )
        config_path = found

    parser = configparser.ConfigParser()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except configparser.Error as exc:
        raise ConfigurationError(
            f"Failed to parse configuration file '{config_path}': {exc}"
        ) from exc
    except Exception as exc:
        raise ConfigurationError(
            f"Could not read configuration file '{config_path}': {exc}"
        ) from exc

    # Validate [ethermig] section
    if "ethermig" not in parser:
        raise ConfigurationError(
            f"Missing required section '[ethermig]' in {config_path.name}"
        )

    ethermig_sec = parser["ethermig"]
    alembic_config_str = ethermig_sec.get("alembic_config", "").strip()
    if not alembic_config_str:
        raise ConfigurationError(
            f"Missing 'alembic_config' in '[ethermig]' section of {config_path.name}"
        )

    models_output_str = ethermig_sec.get("models_output", "").strip()
    if not models_output_str:
        raise ConfigurationError(
            f"Missing 'models_output' in '[ethermig]' section of {config_path.name}"
        )

    # Validate [environments] section
    if "environments" not in parser:
        raise ConfigurationError(
            f"Missing required section '[environments]' in {config_path.name}"
        )

    env_sec = parser["environments"]
    environments: dict[str, str] = {}
    for key, value in env_sec.items():
        val = value.strip()
        if val:
            environments[key] = val

    if not environments:
        raise ConfigurationError(
            f"No environments configured in '[environments]' section of {config_path.name}"
        )

    project_root = config_path.parent
    alembic_path = (project_root / alembic_config_str).resolve()
    models_path = (project_root / models_output_str).resolve()

    return EthermigConfig(
        config_path=config_path,
        alembic_config_path=alembic_path,
        models_output_path=models_path,
        environments=environments,
    )


def create_default_config(target_dir: Optional[Path] = None) -> tuple[bool, Path]:
    """Create default ethermig.ini in target_dir if it does not already exist.

    Returns:
        (created, path): True if file was created, False if file already existed.
    """
    root = (target_dir or Path.cwd()).resolve()
    config_file = root / CONFIG_FILENAME

    if config_file.exists():
        return False, config_file

    config_file.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return True, config_file
