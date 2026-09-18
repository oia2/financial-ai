"""Ключ исхода сбора включает сессию: прогон охватывает несколько дней.

Прежний ключ ``(run_id, source_id)`` допускал в прогоне только ОДНУ сессию на
источник, и догон был вынужден заводить новый идентификатор на каждый день.
Журнал же группирует исходы по прогону и считает в нём сессии: догон из
восьмидесяти двух сессий показывался восемьюдесятью двумя прогонами по одному
дню, а список последних прогонов вмещал пять последних дней вместо пяти
последних прогонов (spec 008, FR-052).

``NULLS NOT DISTINCT`` обязателен: сессия у исхода может отсутствовать —
календарь собирается вне сессии, — и без него два таких исхода одного прогона
считались бы разными строками, то есть ключа бы не было вовсе.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("uq_ingest_run_source", "market_ingest_run", type_="unique")
    op.execute(
        "ALTER TABLE market_ingest_run "
        "ADD CONSTRAINT uq_ingest_run_source "
        "UNIQUE NULLS NOT DISTINCT (run_id, source_id, session_date)"
    )


def downgrade() -> None:
    op.drop_constraint("uq_ingest_run_source", "market_ingest_run", type_="unique")
    # Обратный переход требует единственности пары: лишние исходы прогона
    # удаляются, остаётся самый поздний по началу.
    op.execute(
        """
        DELETE FROM market_ingest_run a
        USING market_ingest_run b
        WHERE a.run_id = b.run_id
          AND a.source_id = b.source_id
          AND (a.started_at, a.id) < (b.started_at, b.id)
        """
    )
    op.create_unique_constraint(
        "uq_ingest_run_source", "market_ingest_run", ["run_id", "source_id"]
    )
