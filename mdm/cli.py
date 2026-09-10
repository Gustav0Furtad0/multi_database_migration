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

from mdm.config import create_default_config, load_config, mask_url
from mdm.core import (
    check_migration_sync,
    downgrade_database,
    generate_migration,
    get_engine,
    get_environment_status,
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
) -> None:
    """Setup MDM configuration and verify connectivity across configured environments."""
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

    alembic_exists = config.alembic_config_path.is_file()
    if alembic_exists:
        console.print("[bold green]✓[/bold green] Alembic configuration found\n")
    else:
        console.print(
            f"[bold red]✗[/bold red] Alembic configuration NOT found at: [dim]{config.alembic_config_path}[/dim]\n"
        )

    table = Table(title=None, show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Environment", min_width=16)
    table.add_column("Status", min_width=24)

    has_failures = not alembic_exists

    for env_name, db_url in config.environments.items():
        connected, err = test_connection(db_url)
        if connected:
            table.add_row(f"[cyan]{env_name}[/cyan]", "[green]✓ Connected[/green]")
        else:
            has_failures = True
            msg = err or "Can't connect to this DB."
            table.add_row(f"[cyan]{env_name}[/cyan]", f"[red]✗ {msg}[/red]")

    console.print(table)
    console.print()

    if has_failures:
        raise typer.Exit(code=1)
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
