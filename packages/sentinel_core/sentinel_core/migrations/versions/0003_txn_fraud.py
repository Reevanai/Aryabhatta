"""Transaction fraud tables.

Revision ID: 0003
Revises: 0002
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    # asyncpg runs a multi-statement script only through its simple-query path.
    driver = op.get_bind().connection.driver_connection
    if driver is None:
        raise RuntimeError("no driver connection")
    await_only(driver.execute(sql))


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS txn_fraud CASCADE")
