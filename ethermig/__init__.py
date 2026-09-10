"""ethermig: Multi-environment database migration CLI and library.

Built on top of Alembic, SQLAlchemy, and SQLModel.
"""

from ethermig.config import EthermigConfig, create_default_config, load_config, mask_url
from ethermig.core import (
    check_migration_sync,
    downgrade_database,
    generate_migration,
    get_alembic_config,
    get_ancestor_revisions,
    get_current_heads,
    get_engine,
    get_environment_status,
    get_script_directory,
    stamp_database,
    test_connection,
    upgrade_database,
)
from ethermig.exceptions import (
    AlembicOperationError,
    ConfigurationError,
    DatabaseConnectionError,
    EthermigError,
    GenerationError,
    MigrationSyncError,
)
from ethermig.generator import (
    generate_sqlmodel_code,
    reverse_engineer_database,
)

__version__ = "0.1.0"

__all__ = [
    "EthermigError",
    "ConfigurationError",
    "DatabaseConnectionError",
    "MigrationSyncError",
    "GenerationError",
    "AlembicOperationError",
    "EthermigConfig",
    "load_config",
    "create_default_config",
    "mask_url",
    "get_engine",
    "test_connection",
    "get_alembic_config",
    "get_script_directory",
    "get_current_heads",
    "get_ancestor_revisions",
    "get_environment_status",
    "check_migration_sync",
    "generate_migration",
    "upgrade_database",
    "downgrade_database",
    "stamp_database",
    "generate_sqlmodel_code",
    "reverse_engineer_database",
]
