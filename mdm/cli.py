"""Command-line interface for MDM using Typer and Rich."""

from pathlib import Path
import sys
from typing import Optional

# Ensure UTF-8 output encoding across all platforms and terminals (e.g. Windows cp1252)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

from mdm import __version__
from mdm.config import create_default_config, load_config, mask_url
from mdm.core import (
    check_migration_sync,
    downgrade_database,
    generate_migration,
    get_engine,
    get_environment_status,
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
from mdm.generator import reverse_engineer_database

app = typer.Typer(
    name="mdm",
    help="MDM: Multi-Environment Database Migration CLI built on Alembic, SQLAlchemy, and SQLModel",
    no_args_is_help=True,
    add_completion=False,
)
setup_app = typer.Typer(
    help="Initialize configuration or verify/reverse-engineer database schemas.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(setup_app, name="setup")

console = Console()
err_console = Console(stderr=True)


def version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold cyan]MDM[/bold cyan] version [green]{__version__}[/green]")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """MDM: Multi-Environment Database Migration CLI built on Alembic, SQLAlchemy, and SQLModel."""
    return


def handle_error(exc: Exception) -> None:
    """Render domain errors with Rich without tracebacks and exit with code 1."""
    if isinstance(exc, MigrationSyncError):
        err_console.print("\n[bold red]Migration generation blocked.[/bold red]\n")
        err_console.print(f"Reference: [cyan]{exc.reference_env}[/cyan]")
        err_console.print(f"Revision:  [cyan]{exc.reference_revision or '<base>'}[/cyan]\n")

        if exc.unreachable_envs:
            err_console.print("[yellow]Unreachable environment(s):[/yellow]")
            for env in exc.unreachable_envs:
                err_console.print(f"  - [bold]{env}[/bold]: Cannot establish connection")
            err_console.print("")

        if exc.unapplied_by_env:
            for env, revs in exc.unapplied_by_env.items():
                err_console.print(f"[bold red]{env}[/bold red] contains unapplied migration(s):")
                for r in revs:
                    err_console.print(f"  - [red]{r}[/red]")
            err_console.print("")

        err_console.print("[yellow]Synchronize the reference environment before generating a new migration.[/yellow]\n")
    elif isinstance(exc, MDMError):
        err_console.print(f"[bold red]Error:[/] {exc.message}")
        if exc.details:
            err_console.print(f"[dim]{exc.details}[/dim]")
    else:
        err_console.print(f"[bold red]Unexpected error:[/] {exc}")

    raise typer.Exit(code=1)


@setup_app.callback()
def setup_main(
    ctx: typer.Context,
    file: bool = typer.Option(
        False,
        "--file",
        help="Create a default mdm.ini configuration in the current directory if one does not exist.",
    ),
    env: Optional[str] = typer.Option(
        None,
        "--env",
        help="Target environment to reverse-engineer models from (defaults to db_local or first connected environment).",
    ),
    models: bool = typer.Option(
        True,
        "--models/--no-models",
        help="Automatically reverse-engineer database schema into the configured models file.",
    ),
    init_alembic: bool = typer.Option(
        True,
        "--init-alembic/--no-init-alembic",
        help="Automatically initialize Alembic configuration and migration directory if missing.",
    ),
) -> None:
    """Setup MDM configuration, initialize Alembic, and generate SQLModel models."""
    if ctx.invoked_subcommand is not None:
        return

    if file:
        created, path = create_default_config()
        if created:
            console.print(f"[bold green]✓[/bold green] Created default configuration file at [cyan]{path}[/cyan]")
        else:
            console.print(f"[yellow]Configuration file already exists at [cyan]{path}[/cyan]. File was not overwritten.[/yellow]")
        raise typer.Exit(code=0)

    # Validate configuration and verify DB connections
    try:
        config = load_config()
    except ConfigurationError as exc:
        handle_error(exc)

    console.print("\n[bold]MDM configuration[/bold]")
    console.print("────────────────────────────────")
    console.print("[bold green]✓[/bold green] Configuration valid")

    table = Table(title=None, show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Environment", min_width=16)
    table.add_column("Status", min_width=24)

    connected_envs: list[str] = []
    has_failures = False

    for env_name, db_url in config.environments.items():
        connected, err = test_connection(db_url)
        if connected:
            table.add_row(f"[cyan]{env_name}[/cyan]", "[green]✓ Connected[/green]")
            connected_envs.append(env_name)
        else:
            has_failures = True
            msg = err or "Can't connect to this DB."
            table.add_row(f"[cyan]{env_name}[/cyan]", f"[red]✗ {msg}[/red]")

    console.print()
    console.print(table)
    console.print()

    # Check or initialize Alembic configuration
    alembic_exists = config.alembic_config_path.is_file()
    if alembic_exists:
        console.print(f"[bold green]✓[/bold green] Alembic configuration found at: [cyan]{config.alembic_config_path}[/cyan]")
    elif init_alembic:
        ini_path, script_dir = init_alembic_environment(config)
        console.print(
            f"[bold green]✓[/bold green] Initialized Alembic migration environment ([cyan]{ini_path.name}[/cyan], [cyan]{script_dir.name}/env.py[/cyan])"
        )
    else:
        has_failures = True
        console.print(
            f"[bold red]✗[/bold red] Alembic configuration NOT found at: [dim]{config.alembic_config_path}[/dim]"
        )

    # Automatically reverse-engineer database models
    if models:
        if not connected_envs:
            console.print("[yellow]⚠ Skipping model generation: no configured database environment is currently reachable.[/yellow]")
        else:
            target_env: Optional[str] = None
            if env:
                if env not in config.environments:
                    handle_error(
                        ConfigurationError(
                            f"Environment '{env}' not found in configuration.",
                            config_path=config.config_path,
                        )
                    )
                if env not in connected_envs:
                    handle_error(
                        DatabaseConnectionError(
                            f"Cannot connect to target environment '{env}' to generate models.",
                            environment=env,
                        )
                    )
                target_env = env
            elif "db_local" in connected_envs:
                target_env = "db_local"
            else:
                target_env = connected_envs[0]

            try:
                engine = get_engine(config.get_database_url(target_env))
                with console.status(f"[bold blue]Inspecting database '{target_env}' and generating models...[/bold blue]"):
                    table_count, out_path = reverse_engineer_database(engine, config.models_output_path)
                console.print(
                    f"[bold green]✓[/bold green] Generated SQLModel models in [cyan]{out_path}[/cyan] ([green]{table_count}[/green] table(s) from [cyan]{target_env}[/cyan])"
                )
            except Exception as exc:
                console.print(f"[bold red]✗ Failed to generate models:[/] {exc}")
                has_failures = True

    if has_failures:
        raise typer.Exit(code=1)

    console.print("\n[bold green]Setup completed successfully![/bold green]\n")
    raise typer.Exit(code=0)


@setup_app.command("verify")
def setup_verify(
    env: str = typer.Option(
        "db_local",
        "--env",
        help="Target environment name from mdm.ini to reverse-engineer.",
    ),
) -> None:
    """Reverse-engineer the target database schema into a canonical SQLModel file."""
    try:
        config = load_config()
        db_url = config.get_database_url(env)

        connected, err = test_connection(db_url)
        if not connected:
            raise DatabaseConnectionError(
                f"Cannot connect to database '{env}': {err or 'Connection failed'}",
                environment=env,
            )

        engine = get_engine(db_url)
        try:
            with console.status(f"[bold blue]Inspecting database '{env}' and generating models..."):
                table_count, out_path = reverse_engineer_database(
                    engine, config.models_output_path
                )
        finally:
            engine.dispose()

        console.print(
            f"[bold green]✓[/bold green] Reverse-engineered [bold]{table_count}[/bold] table(s) "
            f"from [cyan]{env}[/cyan] into [cyan]{out_path}[/cyan]"
        )
    except Exception as exc:
        handle_error(exc)


@app.command("current")
def current_command() -> None:
    """Display current Alembic migration status across all configured environments."""
    try:
        config = load_config()
        statuses = get_environment_status(config)

        console.print("\n[bold]Migration status[/bold]")
        console.print("────────────────────────────────────────────\n")

        for s in statuses:
            console.print(f"[bold cyan]{s.name}[/bold cyan]")
            if not s.is_connected:
                err_msg = s.error_message or "Can't connect to this DB."
                console.print(f"  [bold red]✗[/bold red] {err_msg}\n")
                continue

            if not s.revisions:
                console.print("  Revision: [dim]<base>[/dim]")
                console.print("  Down:     [dim]-[/dim]")
                console.print("  Message:  [dim]no migrations applied[/dim]\n")
                continue

            for rev in s.revisions:
                console.print(f"  Revision: [green]{rev.revision}[/green]")
                down_str = (
                    ", ".join(rev.down_revision)
                    if isinstance(rev.down_revision, tuple)
                    else (rev.down_revision or "<base>")
                )
                console.print(f"  Down:     {down_str}")
                console.print(f"  Message:  {rev.message or '<no description>'}")
            console.print()

    except Exception as exc:
        handle_error(exc)


@app.command("generate")
def generate_command(
    message: str = typer.Option(
        ...,
        "-m",
        "--message",
        help="Migration revision description/message.",
    ),
    env: str = typer.Option(
        "db_local",
        "--env",
        help="Reference database environment name.",
    ),
) -> None:
    """Autogenerate a new Alembic migration after verifying synchronization with remote environments."""
    try:
        config = load_config()
        with console.status(f"[bold blue]Checking migration synchronization and generating revision..."):
            rev_id, rev_path = generate_migration(config, message=message, reference_env=env)

        console.print(f"[bold green]✓[/bold green] Migration generated successfully!")
        console.print(f"  Revision: [bold cyan]{rev_id}[/bold cyan]")
        console.print(f"  Path:     [cyan]{rev_path}[/cyan]")
    except Exception as exc:
        handle_error(exc)


@app.command("upgrade")
def upgrade_command(
    revision: str = typer.Argument(
        "head",
        help="Alembic revision target (default: 'head').",
    ),
    env: str = typer.Option(
        "db_local",
        "--env",
        help="Target database environment name.",
    ),
) -> None:
    """Upgrade database to a specified migration revision."""
    try:
        config = load_config()
        with console.status(f"[bold blue]Upgrading '{env}' to '{revision}'..."):
            upgrade_database(config, env_name=env, revision=revision)
        console.print(f"[bold green]✓[/bold green] Upgraded [cyan]{env}[/cyan] to [bold cyan]{revision}[/bold cyan]")
    except Exception as exc:
        handle_error(exc)


@app.command("downgrade")
def downgrade_command(
    revision: str = typer.Argument(
        "-1",
        help="Alembic revision target (default: '-1').",
    ),
    env: str = typer.Option(
        "db_local",
        "--env",
        help="Target database environment name.",
    ),
) -> None:
    """Downgrade database to a specified migration revision."""
    try:
        config = load_config()
        with console.status(f"[bold blue]Downgrading '{env}' to '{revision}'..."):
            downgrade_database(config, env_name=env, revision=revision)
        console.print(f"[bold green]✓[/bold green] Downgraded [cyan]{env}[/cyan] to [bold cyan]{revision}[/bold cyan]")
    except Exception as exc:
        handle_error(exc)


@app.command("stamp")
def stamp_command(
    revision: str = typer.Argument(
        "head",
        help="Alembic revision target to stamp (default: 'head').",
    ),
    env: str = typer.Option(
        "db_local",
        "--env",
        help="Target database environment name.",
    ),
) -> None:
    """Stamp database with a migration revision without running DDL."""
    try:
        config = load_config()
        with console.status(f"[bold blue]Stamping '{env}' with '{revision}'..."):
            stamp_database(config, env_name=env, revision=revision)
        console.print(f"[bold green]✓[/bold green] Stamped [cyan]{env}[/cyan] with [bold cyan]{revision}[/bold cyan]")
    except Exception as exc:
        handle_error(exc)


if __name__ == "__main__":
    app()
