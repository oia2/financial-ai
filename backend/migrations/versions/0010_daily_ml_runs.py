"""Прогоны Daily ML, их результаты и размер лота.

Три изменения одной миграцией, потому что они вводятся одной фичей.

**Прогон хранится, в отличие от состояния догона.** Догон намеренно держит своё
состояние в процессе: хранимое «идёт» переживает падение и блокирует запуск
навсегда. Здесь хранить обязаны — это история принятых решений, — поэтому
болезнь лечится явно: при старте оставшиеся `running` переводятся в `failed`.

**Уникальный индекс — это ключ идемпотентности**, а не украшение схемы.
Проверка «сначала посмотрим, потом вставим» между двумя процессами не атомарна;
уникальный индекс атомарен по устройству. Четыре поля: дата решения, дайджест
набора, идентификатор и версия модели.

**Скор — NUMERIC(28,9)**, как денежные величины проекта. Он участвует в
сортировке, и float сделал бы порядок зависимым от представления.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_ml_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asof_date", sa.Date(), nullable=False),
        sa.Column("dataset_digest", sa.String(80), nullable=False),
        # Набор удаляется ретеншеном через 30 дней, ссылка остаётся: по ней
        # видно, что именно уходило модели, даже когда файлов уже нет.
        sa.Column("dataset_ref", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        # queued | running | success | failed. Больше состояний нет:
        # пауза принадлежит режиму, устаревание вычисляется.
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        # Причина, пригодная для показа человеку. Текст исключения сюда не
        # попадает: он несёт адрес обращения, возможно внутренний.
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("included_asset_count", sa.Integer(), nullable=True),
        # Окно, полнота и признак эмуляции хранятся, а не вычисляются при
        # показе: глубина окна — настройка и может измениться, полнота входа
        # меняется с приходом данных, а звено однажды перестанет быть
        # эмулятором. Прогон описывает то, что было.
        sa.Column("window_from", sa.Date(), nullable=True),
        sa.Column("window_till", sa.Date(), nullable=True),
        sa.Column("input_complete", sa.Boolean(), nullable=True),
        sa.Column("emulated", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asof_date",
            "dataset_digest",
            "model_id",
            "model_version",
            name="uq_daily_ml_run_input",
        ),
    )
    op.create_index(
        "ix_daily_ml_run_asof",
        "daily_ml_run",
        [sa.text("asof_date DESC")],
    )
    op.create_index("ix_daily_ml_run_status", "daily_ml_run", ["status"])

    op.create_table(
        "daily_ml_ranking_item",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("price_series_id", sa.String(64), nullable=False),
        sa.Column("score", sa.Numeric(28, 9), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["daily_ml_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "rank"),
    )
    op.create_index(
        "ix_daily_ml_ranking_item_asset",
        "daily_ml_ranking_item",
        ["run_id", "asset_id"],
    )

    # NULL означает «лот не получен», а не «единица»: актив без лота в план не
    # попадает, и это объясняется, а не подменяется догадкой.
    op.add_column("market_asset", sa.Column("lot_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("market_asset", "lot_size")
    op.drop_index("ix_daily_ml_ranking_item_asset", table_name="daily_ml_ranking_item")
    op.drop_table("daily_ml_ranking_item")
    op.drop_index("ix_daily_ml_run_status", table_name="daily_ml_run")
    op.drop_index("ix_daily_ml_run_asof", table_name="daily_ml_run")
    op.drop_table("daily_ml_run")
