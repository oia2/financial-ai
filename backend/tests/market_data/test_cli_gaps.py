"""Тесты команды просмотра пропусков.

Требование FR-018 буквально: перечень пропущенных сессий должен быть доступен
**без чтения логов**. Поэтому проверяется именно вывод команды, а не внутреннее
состояние — сегодня дыру нельзя обнаружить иначе как случайно, и вывод здесь и
есть способ её обнаружить.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.config import get_settings
from financial_ai.market_data import cli, groups
from financial_ai.market_data.models import SourceWorkEvidence
from financial_ai.market_data.repository import DailyBar, MarketDataRepository
from financial_ai.market_data.sources import equity_d1
from tests.market_data.verified import record_verified_run

pytestmark = pytest.mark.db

SESSIONS = [
    dt.date(2026, 8, 26),
    dt.date(2026, 8, 27),
    dt.date(2026, 8, 28),
    dt.date(2026, 8, 31),
]
ASOF = SESSIONS[-1]


@pytest.fixture(autouse=True)
def _window() -> None:
    """Окно поиска сужено до засеянных сессий: иначе в него попадёт пустота."""
    get_settings.cache_clear()


def _bar(day: dt.date) -> DailyBar:
    return DailyBar(
        asset_id="EQ_AST_SBER",
        price_series_id="EQ_PRS_SBER",
        session_date=day,
        open=Decimal("312.4"),
        high=Decimal("315.1"),
        low=Decimal("311.0"),
        close=Decimal("314.22"),
        volume=Decimal("1000"),
    )


async def _seed(session: AsyncSession, collected: list[dt.date]) -> MarketDataRepository:
    repository = MarketDataRepository(session)
    await repository.add_trading_sessions(SESSIONS)
    await repository.upsert_asset("EQ_AST_SBER", "SBER", ASOF)
    await repository.upsert_price_series("EQ_PRS_SBER", "EQ_AST_SBER", ASOF)
    if collected:
        await repository.upsert_daily_bars([_bar(day) for day in collected])
        moment = dt.datetime.now(dt.UTC)
        for day in collected:
            for source_id in groups.source_ids_for(groups.GROUPS):
                await record_verified_run(
                    repository,
                    run_id=f"seed-{source_id}-{day}",
                    source_id=source_id,
                    started_at=moment,
                    finished_at=moment,
                    session_date=day,
                    rows_written=1,
                )
    await session.commit()
    return repository


async def test_gaps_lists_missing_sessions(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _seed(db_session, [SESSIONS[0], SESSIONS[3]])

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "пропущено сессий: 2" in out
    assert SESSIONS[1].isoformat() in out
    assert SESSIONS[2].isoformat() in out


async def test_gaps_reports_full_window(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _seed(db_session, SESSIONS)

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "пропущенных сессий нет" in out


async def test_gaps_reports_empty_storage(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустое хранилище — не дыра: это первичная загрузка."""
    await _seed(db_session, [])

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "нужна первичная загрузка" in out


async def test_gaps_shows_unfinished_sources_with_reason(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Незакрытый источник виден так же, как сбой сбора, и с причиной."""
    repository = await _seed(db_session, SESSIONS)
    # Работа позиций за эту сессию не доказана: закрывают доказательства, а
    # не статус прогона (FR-032f).
    await db_session.execute(
        delete(SourceWorkEvidence).where(
            SourceWorkEvidence.source_id == "futures_positions",
            SourceWorkEvidence.session_date == SESSIONS[1],
        )
    )
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-1",
        source_id="futures_positions",
        status="failed",
        started_at=moment,
        finished_at=moment,
        session_date=SESSIONS[1],
        failure_reason="источник не ответил",
        trigger="catchup",
    )
    await db_session.commit()

    code = await cli._gaps(ASOF)
    out = capsys.readouterr().out

    assert code == 0
    assert "незакрыто по источникам:" in out
    assert "futures_positions" in out
    assert "источник не ответил" in out


async def test_gaps_without_calendar_fails(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await cli._gaps(None)
    out = capsys.readouterr().out

    assert code == 1
    assert "календарь пуст" in out


async def test_stats_shows_what_triggered_the_run(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Иначе прогон догона за вчера неотличим от обычного."""
    repository = await _seed(db_session, SESSIONS)
    moment = dt.datetime.now(dt.UTC)
    await repository.record_run(
        run_id="run-1",
        source_id=equity_d1.SOURCE_ID,
        status="ok",
        started_at=moment,
        finished_at=moment,
        session_date=SESSIONS[1],
        rows_written=288,
        trigger="catchup",
    )
    await db_session.commit()

    code = await cli._stats(SESSIONS[1])
    out = capsys.readouterr().out

    assert code == 0
    assert "catchup" in out


# --- команда как тонкий клиент (FR-010) --------------------------------------


class FakeWorker:
    """Подделка внутреннего интерфейса worker'а.

    Команда обязана быть клиентом, а не вторым исполнителем сбора: иначе
    «следить» означало бы смотреть в собственный терминал, а интерфейс, когда
    появится, управлять догоном не смог бы.
    """

    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[str, str, object]] = []

    def __call__(
        self,
        method: str,
        url: str,
        json: object = None,
        params: object = None,
        timeout: float = 0,
    ) -> httpx.Response:
        self.calls.append((method, url, json))
        return httpx.Response(self.status_code, json=self.payload)


async def test_catchup_command_asks_the_worker_and_collects_nothing(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбор идёт в worker, а не в процессе команды."""
    await _seed(db_session, [SESSIONS[0], SESSIONS[3]])
    worker = FakeWorker(
        {
            "status": "running",
            "groups": ["quotes"],
            "date_from": SESSIONS[1].isoformat(),
            "date_till": SESSIONS[2].isoformat(),
            "clamped": False,
            "requested_sessions": 2,
        }
    )
    monkeypatch.setattr(cli.httpx, "request", worker)

    code = cli._catchup(["quotes"], SESSIONS[1], SESSIONS[2])
    out = capsys.readouterr().out

    assert code == 0
    assert "догон запущен: сессий 2" in out

    method, url, payload = worker.calls[0]
    assert method == "POST"
    assert url.endswith("/internal/catchup")
    assert payload == {
        "groups": ["quotes"],
        "date_from": SESSIONS[1].isoformat(),
        "date_till": SESSIONS[2].isoformat(),
    }

    # Ничего не собралось: команда не является вторым сборщиком.
    repository = MarketDataRepository(db_session)
    assert await repository.sessions_with_daily_bars(SESSIONS) == {SESSIONS[0], SESSIONS[3]}


def test_catchup_reports_a_clamped_range(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обрезку диапазона человек должен увидеть, а не угадать."""
    worker = FakeWorker(
        {
            "status": "running",
            "groups": ["quotes"],
            "date_from": SESSIONS[0].isoformat(),
            "date_till": SESSIONS[3].isoformat(),
            "clamped": True,
            "requested_sessions": 4,
        }
    )
    monkeypatch.setattr(cli.httpx, "request", worker)

    assert cli._catchup(None, dt.date(2020, 1, 1), None) == 0
    assert "обрезан по окну" in capsys.readouterr().out


def test_catchup_status_shows_progress(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli.httpx,
        "request",
        FakeWorker(
            {
                "status": "running",
                "requested": 62,
                "closed": 17,
                "failed": 0,
                "remaining": 45,
                "current": SESSIONS[1].isoformat(),
                "reason": None,
            }
        ),
    )

    assert cli._catchup_status() == 0
    out = capsys.readouterr().out
    assert "состояние: running" in out
    assert "осталось:  45" in out


def test_catchup_stop_asks_the_worker(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Остановка мягкая, и команда сообщает именно это."""
    worker = FakeWorker({"status": "stopping", "current": SESSIONS[1].isoformat()})
    monkeypatch.setattr(cli.httpx, "request", worker)

    assert cli._catchup_stop() == 0
    assert worker.calls[0][0] == "DELETE"
    assert "доводится до конца" in capsys.readouterr().out


def test_refusal_is_not_a_crash(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ сборщика печатается его причиной, а не падением команды."""
    monkeypatch.setattr(
        cli.httpx,
        "request",
        FakeWorker(
            {"detail": {"code": "unknown_group", "message": "неизвестная группа 'x'"}},
            status_code=422,
        ),
    )

    assert cli._catchup(None, None, None) == 1
    assert "неизвестная группа" in capsys.readouterr().out
