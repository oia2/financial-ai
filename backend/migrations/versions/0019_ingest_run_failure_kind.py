"""Причина незавершённости исхода сбора из закрытого перечня (spec 008, FR-033f).

Прежде «остановлено человеком», «оборвано перезапуском» и «источник не ответил»
различались только текстом причины, и сводка называла все три «ошибкой
источника». Старые записи размечаются по тексту причины один раз; новые получают
причину в момент записи.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("market_ingest_run", sa.Column("failure_kind", sa.String(16), nullable=True))
    # Разметка прежних записей. Порядок важен: сначала признаки остановки и
    # перезапуска, затем сбой обработки (текст исключения записывался через
    # repr), всё прочее неуспешное — источник.
    op.execute(
        r"""
        UPDATE market_ingest_run SET failure_kind = CASE
            WHEN failure_reason = 'прогон прерван' THEN 'interrupted'
            WHEN status = 'stopped' THEN 'stopped'
            WHEN failure_reason ~ '^[A-Za-z_]+(Error|Exception)\(' THEN 'internal'
            ELSE 'source'
        END
        WHERE status IN ('failed', 'stopped')
        """
    )


def downgrade() -> None:
    op.drop_column("market_ingest_run", "failure_kind")
