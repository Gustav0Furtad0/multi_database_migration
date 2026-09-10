"""MDM: Multi-Environment Database Migration CLI and library.

Built on top of Alembic, SQLAlchemy, and SQLModel.
"""

from mdm.config import MDMConfig, create_default_config, load_config, mask_url
from mdm.core import (
    check_migration_sync,
    downgrade_database,
    generate_migration,
    get_alembic_config,
    get_ancestor_revisions,
    get_current_heads,
    get_engine,
    get_environment_status,
    get_script_directory,
    init_alembic_environment,
    stamp_database,
    test_connection,
    upgrade_database,
)
from mdm.exceptions import (
    AlembicOperationError,
    ConfigurationError,
    DatabaseConnectionError,
    GenerationError,
    MDMError,
    MigrationSyncError,
)
from mdm.generator import (
    generate_sqlmodel_code,
    reverse_engineer_database,
)

__version__ = "0.1.0"

__all__ = [
    "MDMError",
    "ConfigurationError",
    "DatabaseConnectionError",
    "MigrationSyncError",
    "GenerationError",
    "AlembicOperationError",
    "MDMConfig",
    "load_config",
    "create_default_config",
    "mask_url",
    "get_engine",
    "test_connection",
    "get_alembic_config",
    "get_script_directory",
    "init_alembic_environment",
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
