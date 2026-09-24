"""Вид бумаги: акция или пай фонда (spec 008, FR-060).

С 22.06.2026 биржа торгует фондами на доске TQBR, и сбор, берущий доску
целиком, стал получать их вместе с акциями. Вид — факт биржи: миграция только
заводит поле, заполняет его справочник бумаг. До его первого прохода
готовность модели не наступает (FR-060d).

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("market_asset", sa.Column("security_kind", sa.String(8), nullable=True))
    op.create_check_constraint(
        "ck_market_asset_security_kind",
        "market_asset",
        "security_kind IN ('share', 'fund')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_market_asset_security_kind", "market_asset", type_="check")
    op.drop_column("market_asset", "security_kind")
