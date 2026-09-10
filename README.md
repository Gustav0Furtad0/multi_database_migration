<div align="center">

```text
███╗   ███╗██████╗ ███╗   ███╗
████╗ ████║██╔══██╗████╗ ████║
██╔████╔██║██║  ██║██╔████╔██║
██║╚██╔╝██║██║  ██║██║╚██╔╝██║
██║ ╚═╝ ██║██████╔╝██║ ╚═╝ ██║
╚═╝     ╚═╝╚═════╝ ╚═╝     ╚═╝
```

# MDM — Multi-Database Migration CLI & Library

**A powerful, multi-environment database migration tool built on top of Alembic + SQLAlchemy + SQLModel.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Alembic](https://img.shields.io/badge/Alembic-1.13%2B-red.svg)](https://alembic.sqlalchemy.org/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0%2B-crimson.svg)](https://www.sqlalchemy.org/)
[![SQLModel](https://img.shields.io/badge/SQLModel-0.0.16%2B-forestgreen.svg)](https://sqlmodel.tiangolo.com/)
[![Author: Gustavo Furtado](https://img.shields.io/badge/Author-Gustavo_Furtado-purple.svg)](https://github.com/Gustav0Furtad0)

---

</div>

## 🌟 Highlights

* 🛡️ **Migration Synchronization Guard**: Prevents generating conflicting migrations when remote environments contain unapplied revisions relative to your reference environment.
* 🔒 **Fail-Closed Reliability**: Blocks migration generation if any configured remote database is unreachable, preventing accidental schema divergence.
* 🧬 **SQLModel Reverse-Engineering**: Deterministically reflects live database schemas into clean `SQLModel` code that registers directly onto `SQLModel.metadata`.
* ⚡ **Dynamic Alembic URL Injection**: Zero hardcoded database credentials in `alembic.ini`. Target environments receive dynamic connections at runtime.
* 🔐 **Security First**: Automatically masks passwords across all terminal tables, logs, and error outputs (`postgresql://user:***@host:5432/db`).
* 🖥️ **Rich CLI**: Beautiful terminal output powered by **Typer** and **Rich**, with script-friendly exit codes (`0` for success, `1` for expected failures).

---

## 📑 Table of Contents

- [Installation](#-installation)
- [Project Layout](#-project-layout)
- [Quickstart](#-quickstart)
- [Configuration: ethermig.ini](#-configuration-ethermigini)
- [Alembic Configuration & env.py](#-alembic-configuration--envpy)
- [CLI Reference](#-cli-reference)
  - [1. Configuration & Verification (`setup`)](#1-configuration--verification-setup)
  - [2. Inspect Current Status (`current`)](#2-inspect-current-status-current)
  - [3. Safe Migration Generation (`generate`)](#3-safe-migration-generation-generate)
  - [4. Upgrades & Downgrades (`upgrade` / `downgrade`)](#4-upgrades--downgrades-upgrade--downgrade)
  - [5. Stamping Baselines (`stamp`)](#5-stamping-baselines-stamp)
- [The Migration Sync Guard](#-the-migration-sync-guard)
- [Git Workflow](#-git-workflow)
- [Python Programmatic API](#-python-programmatic-api)
- [Author & License](#-author--license)

---

## 🚀 Installation

MDM requires **Python 3.10+**.

```bash
# Clone the repository
git clone https://github.com/Gustav0Furtad0/multi_database_migration.git
cd multi_database_migration

# Install in editable mode
pip install -e .
```

Verify installation:

```bash
mdm --help
# (alias 'ethermig' is also available)
ethermig --help
```

---

## 📁 Project Layout

When integrating MDM into your service:

```text
my_project/
├── ethermig.ini            # Environment connection strings and paths
├── alembic.ini             # Standard Alembic configuration file
├── alembic/
│   ├── env.py              # Dynamic Alembic environment runner
│   └── versions/           # Generated migration scripts
├── models_generated.py     # Canonical SQLModel definitions (auto-generated)
└── src/
    └── ...
```

---

## ⚡ Quickstart

### 1. Initialize Configuration

Generate a default `ethermig.ini` in your project root:

```bash
mdm setup --file
```

### 2. Verify Connectivity

Check configuration syntax and connectivity across all databases:

```bash
mdm setup
```

```text
ethermig configuration
────────────────────────────────
✓ Configuration valid
✓ Alembic configuration found

Environment      Status
──────────────────────────────
db_local         ✓ Connected
db_dev           ✓ Connected
db_prod          ✓ Connected
```

### 3. Reverse-Engineer Current Database Schema

Reverse-engineer `db_local` into a canonical `models_generated.py` file:

```bash
mdm setup verify --env db_local
```

### 4. Check Current Migration Status

```bash
mdm current
```

### 5. Generate a Migration with Sync Protection

```bash
mdm generate -m "add orders table" --env db_local
```

---

## ⚙️ Configuration: `ethermig.ini`

MDM uses standard `configparser` format:

```ini
[ethermig]
alembic_config = alembic.ini
models_output = models_generated.py

[environments]
db_local = postgresql://app_user:secret_pass@localhost:5432/local_db
db_dev = postgresql://app_user:secret_pass@dev-host:5432/dev_db
db_prod = postgresql://app_user:secret_pass@prod-host:5432/prod_db
```

* Paths are resolved relative to the directory containing `ethermig.ini`.
* Environment names are completely arbitrary (`db_staging`, `qa`, `eu_cluster`, etc.).
* Credentials are automatically masked in console outputs.

---

## 🔌 Alembic Configuration & `env.py`

### `alembic.ini`
Keep your `alembic.ini` clean of hardcoded database URLs:

```ini
[alembic]
script_location = alembic
file_template = %%(rev)s_%%(slug)s
prepend_sys_path = .
timezone = UTC
```

### `alembic/env.py`
MDM includes a reference implementation at [`examples/env.py`](examples/env.py). Point `target_metadata` to `SQLModel.metadata`:

```python
import sys
from pathlib import Path
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project root to sys.path so models_generated can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import SQLModel and the generated models module
from sqlmodel import SQLModel

try:
    import models_generated  # Populates SQLModel.metadata with reflected tables
except ImportError:
    # models_generated.py has not been generated yet via 'mdm setup verify'
    pass

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Expose SQLModel metadata for Alembic autogenerate
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # URL is dynamically injected by MDM
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

---

## 🛠️ CLI Reference

### 1. Configuration & Verification (`setup`)

```bash
# Generate starter ethermig.ini (idempotent, won't overwrite existing file)
mdm setup --file

# Validate config, alembic.ini, and test all database connections
mdm setup

# Reverse-engineer target database into models_generated.py
mdm setup verify --env db_local
```

### 2. Inspect Current Status (`current`)

Shows current revision hashes, down-revisions, and messages for all environments without crashing if one host is down:

```bash
mdm current
```

```text
Migration status
────────────────────────────────────────────

db_local
  Revision: a1b2c3d4
  Down:     9f8e7d6c
  Message:  add users table

db_dev
  Revision: a1b2c3d4
  Down:     9f8e7d6c
  Message:  add users table

db_prod
  Revision: 9f8e7d6c
  Down:     <base>
  Message:  initial schema
```

### 3. Safe Migration Generation (`generate`)

Before generating, MDM verifies that remote environments do not contain unapplied migrations relative to your reference environment:

```bash
mdm generate -m "add payment methods" --env db_local
```

### 4. Upgrades & Downgrades (`upgrade` / `downgrade`)

```bash
# Upgrade default db_local to head
mdm upgrade

# Upgrade specific environment
mdm upgrade head --env db_dev

# Downgrade db_local by 1 revision
mdm downgrade -1 --env db_local

# Downgrade to base
mdm downgrade base --env db_dev
```

### 5. Stamping Baselines (`stamp`)

Updates `alembic_version` table without running DDL statements:

```bash
mdm stamp head --env db_local
mdm stamp 26f1ba233ccf --env db_prod
```

---

## 🛡️ The Migration Sync Guard

The **Sync Guard** prevents accidental migration divergence across environments:

```text
Reference DB (db_local) ───► Ancestors: {A, B}
Remote DB    (db_dev)   ───► Ancestors: {A, B, C}  <-- Unapplied migration C!
```

If `db_dev` contains migration `C` that is missing from `db_local`, running `mdm generate` will be **blocked immediately**:

```text
Migration generation blocked.

Reference: db_local
Revision:   b_hash

db_dev contains unapplied migration(s):
  - c_hash

Synchronize the reference environment before generating a new migration.
```

### Fail-Closed Behavior
If any remote environment cannot be reached, MDM **fails closed** to prevent generating migrations while remote states are unknown.

---

## 🌿 Git Workflow

```text
Live Database (db_local)
        │
        ▼
mdm setup verify --env db_local
        │
        ▼
models_generated.py (Deterministic SQLModel schema)
        │
        ▼
mdm generate -m "description"
        │
        ▼
alembic/versions/xxxx_description.py
        │
        ▼
Commit to Git & Pull Request
        │
        ▼
CI/CD Pipeline: mdm upgrade head --env db_staging
        │
        ▼
Production Deployment: mdm upgrade head --env db_prod
```

---

## 🐍 Python Programmatic API

Use MDM directly in your Python code:

```python
from ethermig import (
    load_config,
    check_migration_sync,
    generate_migration,
    upgrade_database,
    downgrade_database,
    reverse_engineer_database,
    get_engine,
)

# Load configuration
config = load_config()

# Inspect and reverse engineer schema
engine = get_engine(config.get_database_url("db_local"))
reverse_engineer_database(engine, config.models_output_path)

# Verify sync
sync_result = check_migration_sync(config, reference_env="db_local")
if sync_result.is_synced:
    rev_id, path = generate_migration(config, message="auto sync", reference_env="db_local")
    print(f"Generated {rev_id} at {path}")

# Run upgrade
upgrade_database(config, env_name="db_dev", revision="head")
```

---

## 👤 Author & License

* **Author**: [Gustavo Furtado](https://github.com/Gustav0Furtad0)
* **Email**: [gufurtado02@gmail.com](mailto:gufurtado02@gmail.com)
* **Repository**: [https://github.com/Gustav0Furtad0/multi_database_migration](https://github.com/Gustav0Furtad0/multi_database_migration)

Licensed under the **[MIT License](LICENSE)**.

```text
Copyright (c) 2026 Gustavo Furtado <gufurtado02@gmail.com>
```
