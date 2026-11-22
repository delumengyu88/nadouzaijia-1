"""Retired tables retained only to preserve data and Alembic's existing schema.

There are no ORM classes, queries, seed writes, or management routes for them.
Dropping historical data requires a separately reviewed migration.
"""

from sqlalchemy import Column, Integer, MetaData, String, Table


def retain_retired_tables(metadata: MetaData) -> None:
    for name in ("platform_aliases", "program_aliases"):
        Table(
            name,
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("alias", String(200), nullable=False, unique=True),
            Column("canonical_name", String(200), nullable=False),
        )
