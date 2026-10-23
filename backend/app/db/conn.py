import psycopg
from psycopg.rows import dict_row


def connect(dsn: str) -> psycopg.Connection[dict[str, object]]:
    """Open a connection returning rows as dicts."""
    return psycopg.connect(dsn, row_factory=dict_row)
