"""Pytest fixtures for ethermig test suite."""

from pathlib import Path
import shutil
import tempfile
from typing import Generator

from alembic.config import Config
from alembic import command
import pytest
import sqlalchemy as sa


@pytest.fixture
def temp_project(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a fully configured temporary project with Alembic and SQLite test databases."""
    alembic_ini_path = tmp_path / "alembic.ini"
    alembic_dir = tmp_path / "alembic"

    # Initialize Alembic
    cfg = Config()
    cfg.config_file_name = str(alembic_ini_path)
    command.init(cfg, str(alembic_dir))

    # Update env.py to use SQLModel
    env_py = alembic_dir / "env.py"
    env_code = env_py.read_text(encoding="utf-8")
    env_code = env_code.replace(
        "target_metadata = None",
        """
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import SQLModel
try:
    import models_generated  # noqa: F401
except ImportError:
    pass

target_metadata = SQLModel.metadata
""",
    )
    env_code = env_code.replace(
        "compare_type=True,",
        "compare_type=True,\n            render_as_batch=True,",
    )
    env_py.write_text(env_code, encoding="utf-8")

    # Create SQLite databases
    local_db_path = tmp_path / "local.db"
    dev_db_path = tmp_path / "dev.db"
    prod_db_path = tmp_path / "prod.db"

    # Create ethermig.ini
    ethermig_ini = tmp_path / "ethermig.ini"
    ethermig_ini.write_text(
        f"""[ethermig]
alembic_config = alembic.ini
models_output = models_generated.py

[environments]
db_local = sqlite:///{local_db_path.as_posix()}
db_dev = sqlite:///{dev_db_path.as_posix()}
db_prod = sqlite:///{prod_db_path.as_posix()}
""",
        encoding="utf-8",
    )

    yield tmp_path
