"""Database schema reverse-engineering into canonical SQLModel models."""

from collections import defaultdict
from dataclasses import dataclass, field
import keyword
from pathlib import Path
import re
from typing import Any, Optional, Sequence, Union

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.inspection import inspect
from sqlalchemy.types import TypeEngine

from mdm.exceptions import GenerationError

IGNORE_TABLES = {
    "alembic_version",
    "sqlite_sequence",
    "sqlite_stat1",
    "sqlite_stat2",
    "sqlite_stat3",
    "sqlite_stat4",
}


def is_system_table(table_name: str) -> bool:
    """Check whether a table is an internal system or migration table."""
    lower = table_name.lower()
    if lower in IGNORE_TABLES:
        return True
    if lower.startswith("pg_") or lower.startswith("information_schema"):
        return True
    return False


def to_pascal_case(name: str) -> str:
    """Convert a table name (snake_case, kebab-case, or spaced) to PascalCase."""
    # Split by non-alphanumeric characters
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", name) if w]
    if not words:
        return "Model"
    result = "".join(w.capitalize() for w in words)
    # Ensure it starts with a letter
    if result[0].isdigit():
        result = f"Table{result}"
    return result


def sanitize_identifier(name: str) -> str:
    """Convert an arbitrary column name into a valid Python identifier."""
    # Replace non-alphanumeric characters with underscore
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    # If starts with digit, prefix with col_
    if clean and clean[0].isdigit():
        clean = f"col_{clean}"
    # If matches a Python keyword, append underscore
    if keyword.iskeyword(clean) or clean in {"self", "cls"}:
        clean = f"{clean}_"
    # Fallback if empty
    if not clean:
        clean = "col"
    return clean


@dataclass
class ColumnSpec:
    """Specification of a model column."""

    attr_name: str
    db_name: str
    python_type: str
    is_optional: bool
    is_primary_key: bool
    is_unique: bool
    is_indexed: bool
    foreign_key: Optional[str] = None
    server_default: Optional[str] = None
    sa_column_kwargs: dict[str, Any] = field(default_factory=dict)
    comment: Optional[str] = None


@dataclass
class TableSpec:
    """Specification of a reflected database table."""

    class_name: str
    table_name: str
    columns: list[ColumnSpec] = field(default_factory=list)
    composite_indexes: list[dict[str, Any]] = field(default_factory=list)
    composite_unique_constraints: list[dict[str, Any]] = field(default_factory=list)
    composite_foreign_keys: list[dict[str, Any]] = field(default_factory=list)
    has_pseudo_pk: bool = False


class SchemaInspector:
    """Inspects a database engine and produces TableSpec objects."""

    def __init__(self, engine: Engine, schema: Optional[str] = None) -> None:
        self.engine = engine
        self.schema = schema
        self.inspector = inspect(engine)

    def get_python_type(self, sa_type: TypeEngine, imports: set[str]) -> str:
        """Map a SQLAlchemy column type to a Python type string, tracking required imports."""
        # Check datetime types (DateTime before Date)
        if isinstance(sa_type, sa.DateTime):
            imports.add("from datetime import datetime")
            return "datetime"
        if isinstance(sa_type, sa.Date):
            imports.add("from datetime import date")
            return "date"
        if isinstance(sa_type, sa.Time):
            imports.add("from datetime import time")
            return "time"

        # Check numeric types
        if isinstance(sa_type, (sa.Integer, sa.BigInteger, sa.SmallInteger)):
            return "int"
        if isinstance(sa_type, sa.Float):
            return "float"
        if isinstance(sa_type, (sa.Numeric, sa.DECIMAL)):
            imports.add("from decimal import Decimal")
            return "Decimal"

        # Check boolean
        if isinstance(sa_type, sa.Boolean):
            return "bool"

        # Check strings
        if isinstance(sa_type, (sa.String, sa.Text, sa.Unicode, sa.UnicodeText)):
            return "str"

        # Check UUID
        if isinstance(sa_type, sa.Uuid):
            imports.add("from uuid import UUID")
            return "UUID"

        # Check binary
        if isinstance(sa_type, sa.LargeBinary):
            return "bytes"

        # Check JSON
        if isinstance(sa_type, sa.JSON):
            imports.add("from typing import Any, dict")
            return "dict[str, Any]"

        # Check Enum
        if isinstance(sa_type, sa.Enum):
            return "str"

        # Check PostgreSQL specific types if present
        type_name = type(sa_type).__name__
        if "UUID" in type_name:
            imports.add("from uuid import UUID")
            return "UUID"
        if "JSON" in type_name:
            imports.add("from typing import Any, dict")
            return "dict[str, Any]"
        if "ARRAY" in type_name:
            imports.add("from typing import Any, list")
            return "list[Any]"

        # Safe fallback
        imports.add("from typing import Any")
        return "Any"

    def inspect_tables(self, imports: set[str]) -> list[TableSpec]:
        """Inspect all user tables in the database schema."""
        table_names = sorted(self.inspector.get_table_names(schema=self.schema))
        used_class_names: dict[str, int] = {}
        table_specs: list[TableSpec] = []

        for table_name in table_names:
            if is_system_table(table_name):
                continue

            # Generate unique PascalCase class name
            base_class_name = to_pascal_case(table_name)
            if base_class_name in used_class_names:
                used_class_names[base_class_name] += 1
                class_name = f"{base_class_name}_{used_class_names[base_class_name]}"
            else:
                used_class_names[base_class_name] = 1
                class_name = base_class_name

            spec = self._inspect_single_table(table_name, class_name, imports)
            table_specs.append(spec)

        return table_specs

    def _inspect_single_table(self, table_name: str, class_name: str, imports: set[str]) -> TableSpec:
        # Columns
        raw_columns = self.inspector.get_columns(table_name, schema=self.schema)

        # Primary Key
        pk_info = self.inspector.get_pk_constraint(table_name, schema=self.schema) or {}
        pk_columns = set(pk_info.get("constrained_columns") or [])

        # Foreign Keys
        raw_fks = self.inspector.get_foreign_keys(table_name, schema=self.schema) or []
        single_fks: dict[str, str] = {}
        composite_fks: list[dict[str, Any]] = []

        for fk in raw_fks:
            constrained = fk.get("constrained_columns") or []
            referred_table = fk.get("referred_table")
            referred_columns = fk.get("referred_columns") or []
            referred_schema = fk.get("referred_schema")

            if len(constrained) == 1 and len(referred_columns) == 1:
                col_name = constrained[0]
                target_col = referred_columns[0]
                target = f"{referred_table}.{target_col}"
                if referred_schema:
                    target = f"{referred_schema}.{target}"
                single_fks[col_name] = target
            elif len(constrained) > 1 and len(referred_columns) == len(constrained):
                composite_fks.append({
                    "name": fk.get("name"),
                    "constrained_columns": constrained,
                    "referred_table": referred_table,
                    "referred_columns": referred_columns,
                    "referred_schema": referred_schema,
                })

        # Indexes
        raw_indexes = self.inspector.get_indexes(table_name, schema=self.schema) or []
        single_col_indexes: set[str] = set()
        single_col_uniques: set[str] = set()
        composite_indexes: list[dict[str, Any]] = []

        for idx in raw_indexes:
            col_names = idx.get("column_names") or []
            is_unique = bool(idx.get("unique"))
            if len(col_names) == 1:
                col_name = col_names[0]
                if is_unique:
                    single_col_uniques.add(col_name)
                else:
                    single_col_indexes.add(col_name)
            elif len(col_names) > 1:
                composite_indexes.append({
                    "name": idx.get("name"),
                    "column_names": col_names,
                    "unique": is_unique,
                })

        # Unique Constraints
        try:
            raw_uniques = self.inspector.get_unique_constraints(table_name, schema=self.schema) or []
        except Exception:
            raw_uniques = []

        composite_unique_constraints: list[dict[str, Any]] = []
        for uq in raw_uniques:
            cols = uq.get("column_names") or []
            if len(cols) == 1:
                single_col_uniques.add(cols[0])
            elif len(cols) > 1:
                composite_unique_constraints.append({
                    "name": uq.get("name"),
                    "column_names": cols,
                })

        # Handle tables without primary keys (SQLModel requires mapped tables to have PK)
        has_pseudo_pk = False
        if not pk_columns and raw_columns:
            has_pseudo_pk = True
            # Prefer unique columns if present, otherwise all columns
            if single_col_uniques:
                pk_columns = set(single_col_uniques)
            else:
                pk_columns = {c["name"] for c in raw_columns}

        # Build column specifications
        column_specs: list[ColumnSpec] = []
        used_attr_names: dict[str, int] = {}

        for col in raw_columns:
            db_name = col["name"]
            sa_type = col["type"]
            nullable = col.get("nullable", True)
            is_pk = db_name in pk_columns
            default = col.get("default")

            py_type = self.get_python_type(sa_type, imports)

            base_attr = sanitize_identifier(db_name)
            if base_attr in used_attr_names:
                used_attr_names[base_attr] += 1
                attr_name = f"{base_attr}_{used_attr_names[base_attr]}"
            else:
                used_attr_names[base_attr] = 1
                attr_name = base_attr

            # Check if db_name differs from attr_name
            sa_column_kwargs: dict[str, Any] = {}
            if attr_name != db_name:
                sa_column_kwargs["name"] = db_name

            # Optional / nullable handling
            # In SQLModel, primary keys with auto-increment or defaults are typically Optional[type] = Field(default=None, primary_key=True)
            is_optional = bool(nullable or is_pk)

            fk_target = single_fks.get(db_name)
            is_unique = db_name in single_col_uniques and not is_pk
            is_indexed = db_name in single_col_indexes and not is_pk and not is_unique

            comment = None
            if has_pseudo_pk and is_pk:
                comment = "Inferred PK for SQLModel mapping (table has no explicit PK in DB)"

            column_specs.append(
                ColumnSpec(
                    attr_name=attr_name,
                    db_name=db_name,
                    python_type=py_type,
                    is_optional=is_optional,
                    is_primary_key=is_pk,
                    is_unique=is_unique,
                    is_indexed=is_indexed,
                    foreign_key=fk_target,
                    server_default=str(default) if default is not None else None,
                    sa_column_kwargs=sa_column_kwargs,
                    comment=comment,
                )
            )

        # Sort columns deterministically: Primary keys first, then remaining columns
        pks = [c for c in column_specs if c.is_primary_key]
        non_pks = [c for c in column_specs if not c.is_primary_key]
        sorted_columns = pks + non_pks

        # Sort composite constraints deterministically
        composite_indexes.sort(key=lambda x: x.get("name") or "")
        composite_unique_constraints.sort(key=lambda x: x.get("name") or "")
        composite_fks.sort(key=lambda x: x.get("name") or "")

        return TableSpec(
            class_name=class_name,
            table_name=table_name,
            columns=sorted_columns,
            composite_indexes=composite_indexes,
            composite_unique_constraints=composite_unique_constraints,
            composite_foreign_keys=composite_fks,
            has_pseudo_pk=has_pseudo_pk,
        )


class CodeGenerator:
    """Generates Python source code for SQLModel classes from TableSpec objects."""

    def __init__(self, table_specs: list[TableSpec], imports: set[str]) -> None:
        self.table_specs = sorted(table_specs, key=lambda t: t.table_name)
        self.imports = set(imports)

    def generate_source(self) -> str:
        """Generate the complete deterministic Python code."""
        # Always require Field and SQLModel
        base_imports = ["from sqlmodel import Field, SQLModel"]

        # Check if Optional is needed
        needs_optional = any(
            col.is_optional for table in self.table_specs for col in table.columns
        )
        if needs_optional:
            self.imports.add("from typing import Optional")

        # Check if SQLAlchemy table args constraints are needed
        sa_table_args: set[str] = set()
        for table in self.table_specs:
            if table.composite_indexes:
                sa_table_args.add("Index")
            if table.composite_unique_constraints:
                sa_table_args.add("UniqueConstraint")
            if table.composite_foreign_keys:
                sa_table_args.add("ForeignKeyConstraint")

        if sa_table_args:
            sa_names = ", ".join(sorted(sa_table_args))
            self.imports.add(f"from sqlalchemy import {sa_names}")

        # Organize imports
        # Consolidate standard library typing imports
        typing_imports: set[str] = set()
        datetime_imports: set[str] = set()
        other_stdlib: set[str] = set()
        third_party: set[str] = set()

        for imp in self.imports:
            if imp.startswith("from typing import"):
                types_part = imp.replace("from typing import", "").strip()
                for item in types_part.split(","):
                    t = item.strip()
                    if t:
                        typing_imports.add(t)
            elif imp.startswith("from datetime import"):
                dt_part = imp.replace("from datetime import", "").strip()
                for item in dt_part.split(","):
                    d = item.strip()
                    if d:
                        datetime_imports.add(d)
            elif imp.startswith("from decimal import"):
                other_stdlib.add("from decimal import Decimal")
            elif imp.startswith("from uuid import"):
                other_stdlib.add("from uuid import UUID")
            elif imp.startswith("from sqlalchemy import"):
                third_party.add(imp)

        import_lines: list[str] = []

        # Standard library imports
        if datetime_imports:
            import_lines.append(f"from datetime import {', '.join(sorted(datetime_imports))}")
        if other_stdlib:
            for s in sorted(other_stdlib):
                import_lines.append(s)
        if typing_imports:
            import_lines.append(f"from typing import {', '.join(sorted(typing_imports))}")

        # Blank line between stdlib and third-party
        if import_lines:
            import_lines.append("")

        # Third party imports
        for tp in sorted(third_party):
            import_lines.append(tp)
        for bi in base_imports:
            import_lines.append(bi)

        import_block = "\n".join(import_lines)

        # Generate class definitions
        class_blocks: list[str] = []
        for table in self.table_specs:
            class_blocks.append(self._generate_class(table))

        classes_code = "\n\n\n".join(class_blocks)

        header = (
            "# Auto-generated by mdm setup verify\n"
            "# DO NOT EDIT DIRECTLY if you plan to re-generate from database schema.\n"
            "# This file exposes SQLModel.metadata for Alembic autogenerate.\n"
        )

        footer = "\n\n# Canonical metadata consumed by Alembic env.py\nmetadata = SQLModel.metadata\n"

        if classes_code:
            return f"{header}\n{import_block}\n\n\n{classes_code}{footer}"
        else:
            return f"{header}\n{import_block}{footer}"

    def _generate_class(self, table: TableSpec) -> str:
        lines: list[str] = []
        lines.append(f"class {table.class_name}(SQLModel, table=True):")
        lines.append(f'    __tablename__ = "{table.table_name}"')
        lines.append("")

        if table.has_pseudo_pk:
            lines.append("    # Notice: Table has no explicit primary key in database.")
            lines.append("    # Selected columns are marked as primary_key for SQLModel mapping.")
            lines.append("")

        for col in table.columns:
            lines.append(self._generate_column(col))

        # Check __table_args__
        table_args = self._generate_table_args(table)
        if table_args:
            lines.append("")
            lines.append("    __table_args__ = (")
            for arg in table_args:
                lines.append(f"        {arg},")
            lines.append("    )")

        return "\n".join(lines)

    def _generate_column(self, col: ColumnSpec) -> str:
        # Determine type annotation
        if col.is_optional:
            type_str = f"Optional[{col.python_type}]"
        else:
            type_str = col.python_type

        # Build Field(...) parameters
        field_args: list[str] = []

        if col.is_primary_key:
            if col.is_optional:
                field_args.append("default=None")
            field_args.append("primary_key=True")
        elif col.is_optional:
            field_args.append("default=None")

        if col.foreign_key:
            field_args.append(f'foreign_key="{col.foreign_key}"')

        if col.is_unique:
            field_args.append("unique=True")

        if col.is_indexed:
            field_args.append("index=True")

        if col.sa_column_kwargs:
            # Deterministic formatting of sa_column_kwargs
            items = ", ".join(f'"{k}": "{v}"' for k, v in sorted(col.sa_column_kwargs.items()))
            field_args.append(f"sa_column_kwargs={{{items}}}")

        comment_suffix = f"  # {col.comment}" if col.comment else ""

        if field_args:
            args_str = ", ".join(field_args)
            return f"    {col.attr_name}: {type_str} = Field({args_str}){comment_suffix}"
        else:
            return f"    {col.attr_name}: {type_str}{comment_suffix}"

    def _generate_table_args(self, table: TableSpec) -> list[str]:
        args: list[str] = []

        # Composite foreign keys
        for fk in table.composite_foreign_keys:
            cols = [f'"{c}"' for c in fk["constrained_columns"]]
            ref_cols = [f'"{fk["referred_table"]}.{c}"' for c in fk["referred_columns"]]
            cols_str = f"[{', '.join(cols)}]"
            ref_cols_str = f"[{', '.join(ref_cols)}]"
            args.append(f"ForeignKeyConstraint({cols_str}, {ref_cols_str})")

        # Composite unique constraints
        for uq in table.composite_unique_constraints:
            cols = [f'"{c}"' for c in uq["column_names"]]
            name = f', name="{uq["name"]}"' if uq.get("name") else ""
            args.append(f"UniqueConstraint({', '.join(cols)}{name})")

        # Composite indexes
        for idx in table.composite_indexes:
            idx_name = f'"{idx["name"]}"' if idx.get("name") else "None"
            cols = [f'"{c}"' for c in idx["column_names"]]
            extra = ", unique=True" if idx.get("unique") else ""
            args.append(f"Index({idx_name}, {', '.join(cols)}{extra})")

        return args


def generate_sqlmodel_code(engine: Engine, schema: Optional[str] = None) -> str:
    """Inspect the database schema and generate deterministic SQLModel Python code."""
    try:
        imports: set[str] = set()
        inspector = SchemaInspector(engine, schema=schema)
        table_specs = inspector.inspect_tables(imports)
        generator = CodeGenerator(table_specs, imports)
        return generator.generate_source()
    except Exception as exc:
        raise GenerationError(f"Failed to reverse engineer database schema: {exc}") from exc


def reverse_engineer_database(
    engine: Engine,
    output_path: Path,
    schema: Optional[str] = None,
) -> tuple[int, Path]:
    """Reverse engineer database schema into SQLModel source and write to output_path.

    Returns:
        (table_count, output_path)
    """
    code = generate_sqlmodel_code(engine, schema=schema)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(code, encoding="utf-8")
    except Exception as exc:
        raise GenerationError(f"Failed to write generated models to '{output_path}': {exc}") from exc

    # Count reflected tables
    table_count = len(re.findall(r"class\s+\w+\(SQLModel,\s*table=True\):", code))
    return table_count, output_path
