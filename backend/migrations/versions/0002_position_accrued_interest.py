"""Накопленный купонный доход в позиции.

Брокер включает НКД в стоимость облигации и в итог портфеля. Без него
отображаемые суммы расходятся с брокером, а SC-003 не выполняется.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "portfolio_position",
        sa.Column(
            "accrued_interest",
            sa.Numeric(28, 9),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("portfolio_position", "accrued_interest")
