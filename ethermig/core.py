"""Core migration management, Alembic integration, and synchronization logic."""

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Iterable, Optional, Sequence, Union

import alembic.command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import Script, ScriptDirectory
import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from ethermig.config import EthermigConfig, mask_url
from ethermig.exceptions import (
    AlembicOperationError,
    DatabaseConnectionError,
    MigrationSyncError,
)
from ethermig.generator import reverse_engineer_database


@dataclass(frozen=True)
class RevisionInfo:
    """Information about an Alembic migration revision."""

    revision: str
    down_revision: Optional[Union[str, tuple[str, ...]]] = None
    message: Optional[str] = None


@dataclass
class EnvironmentStatus:
    """Status of migrations in a single database environment."""

    name: str
    database_url: str
    masked_url: str
    is_connected: bool
    current_heads: tuple[str, ...] = ()
    revisions: list[RevisionInfo] = field(default_factory=list)
    error_message: Optional[str] = None


@dataclass
class SyncCheckResult:
    """Result of migration synchronization check between reference and remotes."""

    reference_env: str
    reference_heads: tuple[str, ...]
    reference_ancestors: set[str]
    is_synced: bool
    unapplied_by_env: dict[str, list[str]] = field(default_factory=dict)
    unreachable_envs: list[str] = field(default_factory=list)
    details: Optional[str] = None


def get_engine(url: str, timeout: int = 3) -> Engine:
    """Create a SQLAlchemy Engine with a short connection timeout."""
    connect_args: dict[str, Any] = {}
    try:
        sa_url = make_url(url)
        drivername = sa_url.drivername.lower()
        if "postgresql" in drivername or "psycopg" in drivername:
            connect_args["connect_timeout"] = timeout
        elif "mysql" in drivername:
            connect_args["connect_timeout"] = timeout
        elif "sqlite" in drivername:
            connect_args["timeout"] = timeout
    except Exception:
        pass

    return sa.create_engine(url, connect_args=connect_args, pool_pre_ping=True)


def test_connection(url: str, timeout: int = 3) -> tuple[bool, Optional[str]]:
    """Test connection to a database.

    Returns:
        (is_connected, error_message_without_credentials)
    """
    engine = None
    try:
        engine = get_engine(url, timeout=timeout)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return True, None
    except Exception as exc:
        raw_msg = str(exc)
        # Strip potential password leakage
        safe_msg = mask_url(raw_msg)
        # Simplify common connection refused or timeout messages
        if "timeout" in safe_msg.lower():
            return False, "Connection timed out"
        return False, safe_msg.split("\n")[0]
    finally:
        if engine is not None:
            engine.dispose()


def get_alembic_config(config: EthermigConfig, env_name: str) -> Config:
    """Create and configure an Alembic Config object for the given environment."""
    config.validate_alembic_exists()

    alembic_cfg = Config(str(config.alembic_config_path))
    db_url = config.get_database_url(env_name)

    # Dynamically inject target database URL
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # Ensure project root is on sys.path so env.py can import models_generated.py
    project_root_str = str(config.project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    return alembic_cfg


def get_script_directory(config: EthermigConfig) -> ScriptDirectory:
    """Load Alembic ScriptDirectory from the configured alembic.ini."""
    config.validate_alembic_exists()
    alembic_cfg = Config(str(config.alembic_config_path))
    try:
        return ScriptDirectory.from_config(alembic_cfg)
    except Exception as exc:
        raise AlembicOperationError(
            f"Failed to load Alembic migration scripts from '{config.alembic_config_path}': {exc}"
        ) from exc


def get_current_heads(engine: Engine) -> tuple[str, ...]:
    """Retrieve the current migration heads from a database via MigrationContext."""
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            heads = context.get_current_heads()
            return tuple(heads) if heads else ()
    except Exception as exc:
        safe_err = mask_url(str(exc))
        raise DatabaseConnectionError(
            f"Failed to read migration version: {safe_err}"
        ) from exc


def get_ancestor_revisions(script_dir: ScriptDirectory, heads: Iterable[str]) -> set[str]:
    """Traverse the Alembic migration DAG to find all ancestor revisions for the given heads.

    Handles linear history, branches, and merge revisions correctly.
    """
    ancestors: set[str] = set()
    queue = [h for h in heads if h]

    while queue:
        rev_id = queue.pop(0)
        if rev_id in ancestors:
            continue
        ancestors.add(rev_id)

        try:
            script = script_dir.get_revision(rev_id)
        except Exception:
            # If revision cannot be found in local scripts, keep it in ancestors set but can't traverse parents
            continue

        if script and script.down_revision:
            downs = (
                script.down_revision
                if isinstance(script.down_revision, tuple)
                else (script.down_revision,)
            )
            for d in downs:
                if d and d not in ancestors:
                    queue.append(d)

    return ancestors


def get_environment_status(config: EthermigConfig) -> list[EnvironmentStatus]:
    """Inspect migration status across all configured environments."""
    script_dir = None
    try:
        script_dir = get_script_directory(config)
    except Exception:
        # Script directory might fail if alembic.ini is misconfigured, but we still report DB connectivity
        pass

    results: list[EnvironmentStatus] = []

    for env_name, db_url in config.environments.items():
        masked = mask_url(db_url)
        engine = None
        try:
            connected, err = test_connection(db_url)
            if not connected:
                results.append(
                    EnvironmentStatus(
                        name=env_name,
                        database_url=db_url,
                        masked_url=masked,
                        is_connected=False,
                        error_message=err or "Can't connect to this DB.",
                    )
                )
                continue

            engine = get_engine(db_url)
            heads = get_current_heads(engine)

            revisions: list[RevisionInfo] = []
            for h in heads:
                msg = None
                down_rev = None
                if script_dir:
                    try:
                        sc = script_dir.get_revision(h)
                        if sc:
                            msg = sc.doc
                            down_rev = sc.down_revision
                    except Exception:
                        msg = "<revision not in local migration directory>"

                revisions.append(
                    RevisionInfo(revision=h, down_revision=down_rev, message=msg)
                )

            results.append(
                EnvironmentStatus(
                    name=env_name,
                    database_url=db_url,
                    masked_url=masked,
                    is_connected=True,
                    current_heads=heads,
                    revisions=revisions,
                )
            )
        except Exception as exc:
            safe_err = mask_url(str(exc)).split("\n")[0]
            results.append(
                EnvironmentStatus(
                    name=env_name,
                    database_url=db_url,
                    masked_url=masked,
                    is_connected=False,
                    error_message=safe_err or "Can't connect to this DB.",
                )
            )
        finally:
            if engine is not None:
                engine.dispose()

    return results


def check_migration_sync(config: EthermigConfig, reference_env: str) -> SyncCheckResult:
    """Check migration synchronization between the reference environment and remote environments.

    Fails closed if any remote environment is unreachable.

    Safe:
        Reference environment is at or ahead of remote environments in the migration graph.
    Unsafe:
        A remote environment contains revisions that are not ancestors of the reference revisions.
    """
    ref_url = config.get_database_url(reference_env)
    script_dir = get_script_directory(config)

    # Verify reference connection
    ref_connected, ref_err = test_connection(ref_url)
    if not ref_connected:
        raise DatabaseConnectionError(
            f"Cannot connect to reference environment '{reference_env}': {ref_err}",
            environment=reference_env,
        )

    ref_engine = get_engine(ref_url)
    try:
        ref_heads = get_current_heads(ref_engine)
    finally:
        ref_engine.dispose()

    ref_ancestors = get_ancestor_revisions(script_dir, ref_heads)

    unapplied_by_env: dict[str, list[str]] = {}
    unreachable_envs: list[str] = []

    for env_name, db_url in config.environments.items():
        if env_name == reference_env:
            continue

        remote_connected, remote_err = test_connection(db_url)
        if not remote_connected:
            unreachable_envs.append(env_name)
            continue

        remote_engine = get_engine(db_url)
        try:
            remote_heads = get_current_heads(remote_engine)
        finally:
            remote_engine.dispose()

        remote_ancestors = get_ancestor_revisions(script_dir, remote_heads)

        # Detect revisions present in remote but missing from reference
        unapplied = remote_ancestors - ref_ancestors
        if unapplied:
            # Sort deterministically
            unapplied_by_env[env_name] = sorted(list(unapplied))

    is_synced = (len(unapplied_by_env) == 0) and (len(unreachable_envs) == 0)

    return SyncCheckResult(
        reference_env=reference_env,
        reference_heads=ref_heads,
        reference_ancestors=ref_ancestors,
        is_synced=is_synced,
        unapplied_by_env=unapplied_by_env,
        unreachable_envs=unreachable_envs,
    )


def generate_migration(
    config: EthermigConfig,
    message: str,
    reference_env: str = "db_local",
) -> tuple[str, Path]:
    """Execute the full migration generation flow:

    1. Load & validate configuration.
    2. Load Alembic ScriptDirectory.
    3. Check migration synchronization guard (fail-closed if unreachable or unapplied).
    4. Programmatically run reverse engineering on the reference database.
    5. Configure Alembic dynamically.
    6. Generate migration revision via alembic.command.revision(autogenerate=True).
    7. Return revision ID and path.
    """
    # Step 1 & 2: Validate config and load ScriptDirectory
    config.validate_alembic_exists()
    script_dir = get_script_directory(config)

    # Step 3: Migration Sync Guard
    sync_result = check_migration_sync(config, reference_env)
    if not sync_result.is_synced:
        ref_rev_str = ", ".join(sync_result.reference_heads) if sync_result.reference_heads else "<base>"
        raise MigrationSyncError(
            message="Migration generation blocked due to synchronization conflict.",
            reference_env=reference_env,
            reference_revision=ref_rev_str,
            unapplied_by_env=sync_result.unapplied_by_env,
            unreachable_envs=sync_result.unreachable_envs,
        )

    # Step 4: Reverse engineer reference database schema into models_output
    ref_url = config.get_database_url(reference_env)
    ref_engine = get_engine(ref_url)
    try:
        reverse_engineer_database(ref_engine, config.models_output_path)
    finally:
        ref_engine.dispose()

    # Step 5 & 6: Configure Alembic programmatically & autogenerate revision
    alembic_cfg = get_alembic_config(config, reference_env)
    try:
        result = alembic.command.revision(alembic_cfg, message=message, autogenerate=True)
    except Exception as exc:
        raise AlembicOperationError(f"Alembic autogenerate revision failed: {exc}") from exc

    # Step 7: Return revision ID and file path
    if isinstance(result, list):
        script_obj = result[0]
    else:
        script_obj = result

    rev_id = script_obj.revision if script_obj else "unknown"
    rev_path = Path(script_obj.path) if script_obj and script_obj.path else Path()

    return rev_id, rev_path


def upgrade_database(
    config: EthermigConfig,
    env_name: str = "db_local",
    revision: str = "head",
) -> None:
    """Execute Alembic upgrade command for the given environment."""
    alembic_cfg = get_alembic_config(config, env_name)
    try:
        alembic.command.upgrade(alembic_cfg, revision)
    except Exception as exc:
        raise AlembicOperationError(f"Upgrade to '{revision}' failed on '{env_name}': {exc}") from exc


def downgrade_database(
    config: EthermigConfig,
    env_name: str = "db_local",
    revision: str = "-1",
) -> None:
    """Execute Alembic downgrade command for the given environment."""
    alembic_cfg = get_alembic_config(config, env_name)
    try:
        alembic.command.downgrade(alembic_cfg, revision)
    except Exception as exc:
        raise AlembicOperationError(f"Downgrade to '{revision}' failed on '{env_name}': {exc}") from exc


def stamp_database(
    config: EthermigConfig,
    env_name: str = "db_local",
    revision: str = "head",
) -> None:
    """Execute Alembic stamp command for the given environment."""
    alembic_cfg = get_alembic_config(config, env_name)
    try:
        alembic.command.stamp(alembic_cfg, revision)
    except Exception as exc:
        raise AlembicOperationError(f"Stamp '{revision}' failed on '{env_name}': {exc}") from exc
