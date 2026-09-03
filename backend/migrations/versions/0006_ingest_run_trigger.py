"""Чем вызван прогон сбора.

Без этого признака прогон догона за вчерашнюю дату неотличим от обычного, а
ручной сбор за прошлую дату — от автоматического догона. Выводить признак из
разницы между датой сессии и датой выполнения нельзя: приём даёт неверный ответ
в обоих случаях.

Существующие строки получают `daily`. Это верно по факту: другого способа
собрать данные до фичи 004 не было.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Колонка добавляется с умолчанием, чтобы существующие строки заполнились
    # одним запросом, и лишь затем становится обязательной.
    op.add_column(
        "market_ingest_run",
        sa.Column("trigger", sa.String(16), nullable=False, server_default="daily"),
    )
    op.alter_column("market_ingest_run", "trigger", server_default=None)


def downgrade() -> None:
    op.drop_column("market_ingest_run", "trigger")
