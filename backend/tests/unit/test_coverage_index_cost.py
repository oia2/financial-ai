"""Индекс исходов не умножает однодневные записи на всё историческое окно."""

import datetime as dt
from unittest.mock import AsyncMock, Mock, PropertyMock

from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.market_data.models import IngestRun
from financial_ai.market_data.repository import MarketDataRepository


async def test_single_day_outcomes_do_not_scan_every_session() -> None:
    days = [dt.date(2025, 1, 1) + dt.timedelta(days=offset) for offset in range(314)]
    run = Mock(spec=IngestRun, source_id="brent", period_from=None, period_till=None)
    session_date = PropertyMock(return_value=days[-1])
    type(run).session_date = session_date
    row_count = 1000
    result = Mock()
    result.all.return_value = [run] * row_count
    session = AsyncMock(spec=AsyncSession)
    session.scalars.return_value = result

    latest = await MarketDataRepository(session).latest_run_by_session(days)

    assert latest == {(days[-1], "brent"): run}
    # Не секундомер, зависящий от машины: прежний цикл делал 314 000 чтений.
    assert session_date.call_count <= row_count * 2
