import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.fixture
def restore_migration_leaf_nodes(transactional_db):
    """Return the shared test database to the current migration graph after DDL tests."""

    del transactional_db
    leaf_nodes = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        yield
    finally:
        MigrationExecutor(connection).migrate(leaf_nodes)
        connection.close()
