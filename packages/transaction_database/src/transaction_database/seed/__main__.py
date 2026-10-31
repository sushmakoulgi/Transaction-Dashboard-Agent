"""Replace all rows with a fresh sample dataset: `python -m transaction_database.seed`."""

import logging

from transaction_database.connection import database_engine
from transaction_database.seed.sample_data_seeder import SeedSettings, seed_sample_data


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = SeedSettings()  # values come from the environment
    seed_sample_data(database_engine(str(settings.postgres_dsn_owner), settings), settings)


if __name__ == "__main__":
    main()
