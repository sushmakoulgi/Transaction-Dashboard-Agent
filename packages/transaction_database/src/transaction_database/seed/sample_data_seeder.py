"""Writes the generated sample dataset into the database (replacing every row)."""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, insert, text

from shared.config import DatabaseSettings
from transaction_database.orm_models import Base
from transaction_database.seed.sample_data_generator import SampleDataGenerator, SampleDataset

logger = logging.getLogger(__name__)


class SeedSettings(DatabaseSettings):
    business_timezone: str = "America/New_York"
    sample_data_random_seed: int = 20261027


def replace_sample_data(engine: Engine, dataset: SampleDataset) -> None:
    rows_by_table_name = dataset.rows_by_table_name()
    tables_in_foreign_key_order = [
        table for table in Base.metadata.sorted_tables if table.name in rows_by_table_name
    ]
    table_names = ", ".join(table.name for table in tables_in_foreign_key_order)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table_names} CASCADE"))
        for table in tables_in_foreign_key_order:
            rows = rows_by_table_name[table.name]
            if rows:
                connection.execute(insert(table), rows)


def seed_sample_data(engine: Engine, settings: SeedSettings) -> SampleDataset:
    """Replaces all rows with a fresh dataset dated relative to now."""
    dataset = SampleDataGenerator(
        reference_time=datetime.now(UTC),
        business_timezone=ZoneInfo(settings.business_timezone),
        random_seed=settings.sample_data_random_seed,
    ).generate()
    replace_sample_data(engine, dataset)
    logger.info(
        "Seeded %s",
        ", ".join(f"{len(rows)} {name}" for name, rows in dataset.rows_by_table_name().items()),
    )
    return dataset
