"""Команды работы с набором входных данных и звеном ранжирования.

python -m financial_ai.ranking.cli build --asof 2026-08-28
python -m financial_ai.ranking.cli rank --asof 2026-08-28
python -m financial_ai.ranking.cli prune
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from financial_ai.config import get_settings
from financial_ai.db.engine import get_session_factory
from financial_ai.logging import setup_logging
from financial_ai.ranking import client as ranking_client
from financial_ai.ranking import dataset as dataset_module


def _parse_date(raw: str) -> dt.date:
    return dt.date.fromisoformat(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Набор входных данных и ранжирование")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Материализовать набор на дату решения")
    build.add_argument("--asof", type=_parse_date, required=True)

    rank = sub.add_parser("rank", help="Запросить ранжирование на дату решения")
    rank.add_argument("--asof", type=_parse_date, required=True)

    sub.add_parser("prune", help="Удалить наборы старше срока хранения")

    return parser


async def _build(asof: dt.date) -> int:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        try:
            dataset = await dataset_module.build_dataset(session, settings, asof)
        except dataset_module.DatasetError as error:
            print(f"набор не собран: {error}")
            return 1

    print(f"ссылка:   {dataset.ref}")
    print(f"дайджест: {dataset.digest}")
    print(f"сессий:   {len(dataset.sessions)}")
    print(f"активов:  {len(dataset.assets)}")
    return 0


async def _rank(asof: dt.date) -> int:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        try:
            dataset = await dataset_module.build_dataset(session, settings, asof)
        except dataset_module.DatasetError as error:
            print(f"набор не собран: {error}")
            return 1

    try:
        ranking = await ranking_client.request_ranking(settings, dataset)
    except ranking_client.RankingUnavailableError as error:
        # Отдельный код возврата: сбой ранжирования — не сбой сбора данных.
        print(f"ранжирование не получено: {error}")
        return 2

    print(f"дата решения:     {ranking.asof_date}")
    print(f"модель:           {ranking.model_id}")
    print(f"дайджест входа:   {ranking.input_digest}")
    print(f"эмуляция:         {'ДА — значения вымышлены' if ranking.emulated else 'нет'}")
    print(f"вошло активов:    {ranking.included_asset_count}")
    for item in ranking.items[:10]:
        print(f"  {item.rank:>3}  {item.asset_id:<20} {item.score}")
    if len(ranking.items) > 10:
        print(f"  … ещё {len(ranking.items) - 10}")
    for excluded in ranking.excluded[:10]:
        print(f"  исключён: {excluded['asset_id']} — {excluded['reason']}")
    return 0


def _prune() -> int:
    removed = dataset_module.prune_datasets(get_settings())
    print(f"удалено наборов: {removed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(get_settings().log_level)

    if args.command == "build":
        return asyncio.run(_build(args.asof))
    if args.command == "rank":
        return asyncio.run(_rank(args.asof))
    if args.command == "prune":
        return _prune()
    return 1


if __name__ == "__main__":
    sys.exit(main())
