"""Tests for SQLModel reverse engineering in ethermig.generator."""

from pathlib import Path
import pytest
import sqlalchemy as sa
from sqlmodel import SQLModel

from ethermig.generator import (
    generate_sqlmodel_code,
    reverse_engineer_database,
    sanitize_identifier,
    to_pascal_case,
)


def test_naming_helpers():
    """Verify table name PascalCase conversion and column identifier sanitization."""
    assert to_pascal_case("users") == "Users"
    assert to_pascal_case("user_accounts") == "UserAccounts"
    assert to_pascal_case("order-line-items") == "OrderLineItems"
    assert to_pascal_case("123orders") == "Table123orders"
    assert to_pascal_case("") == "Model"

    # Column identifier sanitization
    assert sanitize_identifier("valid_name") == "valid_name"
    assert sanitize_identifier("created-at") == "created_at"
    assert sanitize_identifier("user name") == "user_name"
    assert sanitize_identifier("from") == "from_"
    assert sanitize_identifier("class") == "class_"
    assert sanitize_identifier("def") == "def_"
    assert sanitize_identifier("123col") == "col_123col"


def test_reverse_engineer_various_types(tmp_path: Path):
    """Verify reflection of diverse column types into valid SQLModel definitions."""
    db_path = tmp_path / "types.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
            CREATE TABLE all_types (
                id INTEGER PRIMARY KEY,
                int_col INTEGER,
                float_col REAL,
                decimal_col NUMERIC(10, 2),
                bool_col BOOLEAN,
                str_col VARCHAR(100),
                text_col TEXT,
                date_col DATE,
                datetime_col TIMESTAMP,
                time_col TIME,
                bytes_col BLOB
            )
        """
            )
        )

    code = generate_sqlmodel_code(engine)

    # Check imports
    assert "from sqlmodel import Field, SQLModel" in code
    assert "from datetime import date, datetime, time" in code
    assert "from decimal import Decimal" in code
    assert "from typing import Optional" in code

    # Check class and fields
    assert "class AllTypes(SQLModel, table=True):" in code
    assert '__tablename__ = "all_types"' in code
    assert "id: Optional[int] = Field(default=None, primary_key=True)" in code
    assert "int_col: Optional[int] = Field(default=None)" in code
    assert "float_col: Optional[float] = Field(default=None)" in code
    assert "decimal_col: Optional[Decimal] = Field(default=None)" in code
    assert "bool_col: Optional[bool] = Field(default=None)" in code
    assert "str_col: Optional[str] = Field(default=None)" in code
    assert "text_col: Optional[str] = Field(default=None)" in code
    assert "date_col: Optional[date] = Field(default=None)" in code
    assert "datetime_col: Optional[datetime] = Field(default=None)" in code
    assert "time_col: Optional[time] = Field(default=None)" in code
    assert "bytes_col: Optional[bytes] = Field(default=None)" in code

    # Verify that the generated code is valid Python and registers metadata
    namespace: dict = {}
    exec(code, namespace)
    assert "AllTypes" in namespace
    meta = namespace["SQLModel"].metadata
    assert "all_types" in meta.tables


def test_foreign_keys_and_indexes(tmp_path: Path):
    """Verify reflection of foreign keys, single indexes, and multi-column indexes."""
    db_path = tmp_path / "relations.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
            CREATE TABLE authors (
                id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(100) UNIQUE
            )
        """
            )
        )
        conn.execute(
            sa.text(
                """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                author_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                published_year INTEGER,
                FOREIGN KEY (author_id) REFERENCES authors(id)
            )
        """
            )
        )
        conn.execute(
            sa.text("CREATE INDEX idx_books_author_year ON books(author_id, published_year)")
        )

    code = generate_sqlmodel_code(engine)

    assert "class Authors(SQLModel, table=True):" in code
    assert "class Books(SQLModel, table=True):" in code

    # Foreign key
    assert 'foreign_key="authors.id"' in code

    # Index
    assert "Index" in code
    assert "idx_books_author_year" in code

    # Execute and verify
    namespace: dict = {}
    exec(code, namespace)
    assert "authors" in namespace["SQLModel"].metadata.tables
    assert "books" in namespace["SQLModel"].metadata.tables


def test_composite_primary_key(tmp_path: Path):
    """Verify reflection of composite primary keys."""
    db_path = tmp_path / "composite.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
            CREATE TABLE order_items (
                order_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (order_id, item_id)
            )
        """
            )
        )

    code = generate_sqlmodel_code(engine)
    assert "class OrderItems(SQLModel, table=True):" in code
    assert "order_id: Optional[int] = Field(default=None, primary_key=True)" in code
    assert "item_id: Optional[int] = Field(default=None, primary_key=True)" in code

    namespace: dict = {}
    exec(code, namespace)
    table = namespace["OrderItems"].__table__
    assert [c.name for c in table.primary_key.columns] == ["order_id", "item_id"]


def test_table_without_primary_key(tmp_path: Path):
    """Verify handling of tables without primary keys (infers PK for SQLModel mapping)."""
    db_path = tmp_path / "nopk.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
            CREATE TABLE logs (
                message TEXT,
                level VARCHAR(20)
            )
        """
            )
        )

    code = generate_sqlmodel_code(engine)
    assert "class Logs(SQLModel, table=True):" in code
    assert "Notice: Table has no explicit primary key" in code

    # Must execute without mapper error
    namespace: dict = {}
    exec(code, namespace)
    assert "logs" in namespace["SQLModel"].metadata.tables


def test_sanitized_column_names(tmp_path: Path):
    """Verify handling of hyphenated column names and Python reserved words."""
    db_path = tmp_path / "sanitize.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
            CREATE TABLE weird_names (
                id INTEGER PRIMARY KEY,
                [created-at] TIMESTAMP,
                [from] TEXT,
                [def] INTEGER
            )
        """
            )
        )

    code = generate_sqlmodel_code(engine)

    assert 'created_at: Optional[datetime] = Field(default=None, sa_column_kwargs={"name": "created-at"})' in code
    assert 'from_: Optional[str] = Field(default=None, sa_column_kwargs={"name": "from"})' in code
    assert 'def_: Optional[int] = Field(default=None, sa_column_kwargs={"name": "def"})' in code

    namespace: dict = {}
    exec(code, namespace)
    table = namespace["WeirdNames"].__table__
    assert "created-at" in table.columns
    assert "from" in table.columns
    assert "def" in table.columns


def test_generation_determinism(tmp_path: Path):
    """Verify running reverse engineering twice produces identical code."""
    db_path = tmp_path / "det.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE z_table (id INT PRIMARY KEY, name TEXT)"))
        conn.execute(sa.text("CREATE TABLE a_table (id INT PRIMARY KEY, val REAL)"))

    code1 = generate_sqlmodel_code(engine)
    code2 = generate_sqlmodel_code(engine)
    assert code1 == code2

    # Verify alphabetical table ordering (a_table before z_table)
    a_idx = code1.find("class ATable")
    z_idx = code1.find("class ZTable")
    assert a_idx != -1 and z_idx != -1
    assert a_idx < z_idx


def test_reverse_engineer_database_writes_file(tmp_path: Path):
    """Verify reverse_engineer_database writes to the target file."""
    db_path = tmp_path / "test.db"
    out_file = tmp_path / "models_gen.py"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE items (id INT PRIMARY KEY, title TEXT)"))

    count, path = reverse_engineer_database(engine, out_file)
    assert count == 1
    assert path == out_file
    assert out_file.is_file()
    assert "class Items(SQLModel, table=True):" in out_file.read_text(encoding="utf-8")
