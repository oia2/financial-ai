/**
 * Состояния раздела «Рыночные данные» — contracts/ui-states.md (spec 008).
 *
 * Заменяет прежние проверки состояний догона: раздел переехал на макет
 * `market-data.html`, и язык у него другой. Проверяется не вёрстка, а то, ради
 * чего эти состояния заведены:
 *
 *  - виден идущий источник и следующий за ним (FR-003);
 *  - у каждого пропуска названа причина (FR-002);
 *  - панель не исчезает, когда прогон кончился, и показывает итог (FR-025);
 *  - остановка — состояние, а не уведомление;
 *  - пауза автосбора не обрывает начатый прогон, но видна.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { CatchupStateDto, RunSummaryDto } from '@/entities/market-data';

import { catchupFixture } from './msw/market-data';
import { http, HttpResponse, server } from './msw/server';

function renderWith(state: CatchupStateDto, runs: RunSummaryDto[] = []) {
  server.use(
    http.get('*/api/market-data/catchup', () => HttpResponse.json(state)),
    http.get('*/api/market-data/runs', () => HttpResponse.json({ runs })),
  );

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });

  window.history.pushState(null, '', '/market-data');
  navigate('market-data');

  return render(
    <AppProviders client={client}>
      <AppShell />
    </AppProviders>,
  );
}

const FINISHED_RUN: RunSummaryDto = {
  run_id: 'run-1',
  mode: 'daily',
  started_at: '2026-09-17T14:58:00Z',
  finished_at: '2026-09-17T15:14:00Z',
  status: 'failed',
  sessions: { requested: 12, collected: 10, failed: 1, skipped: 1 },
  failures: [
    {
      source_id: 'brent',
      title: 'Brent',
      session_date: '2026-09-09',
      reason: 'источник не ответил вовремя',
    },
  ],
};

describe('ход прогона', () => {
  it('идёт: назван идущий источник и следующий за ним', async () => {
    renderWith(catchupFixture('running'));

    // Идущий источник — тот, ради которого лента и заведена: без него долгий
    // шаг неотличим от зависания.
    expect(await screen.findByText('Агрегаты торгов')).toBeInTheDocument();
    expect(screen.getByText('идёт')).toBeInTheDocument();
    expect(screen.getByText('следующий')).toBeInTheDocument();
  });

  it('идёт: календарь помечен суточным и в счёт сессии не входит', async () => {
    renderWith(catchupFixture('running'));

    expect(await screen.findByText('раз в сутки')).toBeInTheDocument();
    // Посессионных источников три, отработал один: календарь в счёт не идёт.
    expect(document.querySelector('.rail-head b')?.textContent).toContain('1 из 3');
  });

  it('у каждого пропуска названа причина', async () => {
    renderWith(
      catchupFixture('running', {
        skips: [
          {
            session_date: '2026-09-15',
            reason: 'attempts_exhausted',
            detail: '5 попыток из 5',
          },
        ],
        sessions: {
          requested: 3,
          collected: 1,
          partial: 0,
          failed: 0,
          skipped: 1,
          pending: 1,
          outcomes: [
            { session_date: '2026-09-14', outcome: 'collected' },
            { session_date: '2026-09-15', outcome: 'skipped' },
          ],
        },
      }),
    );

    expect(await screen.findByText('Почему пропущена 1 сессия')).toBeInTheDocument();
    expect(screen.getByText(/исчерпан предел попыток: 5 попыток из 5/)).toBeInTheDocument();
  });

  it('останавливается: состояние, а не только уведомление', async () => {
    renderWith(catchupFixture('stopping', { stop_requested: true }));

    expect(await screen.findByText('Останавливается')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Остановка запрошена' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Остановить прогон' })).not.toBeInTheDocument();
  });
});

describe('прогон закончился', () => {
  it('итог остаётся на месте панели, а не исчезает вместе с процессом', async () => {
    renderWith(catchupFixture('finished'), [FINISHED_RUN]);

    expect(await screen.findByRole('heading', { name: /Прогон закончен/ })).toBeInTheDocument();
    expect(screen.getByText(/18 собрано/)).toBeInTheDocument();
    expect(screen.getByText(/Последние прогоны/)).toBeInTheDocument();
  });

  it('незакрытое можно повторить, и это новый запуск', async () => {
    renderWith(catchupFixture('finished'), [FINISHED_RUN]);

    expect(
      await screen.findByRole('button', { name: 'Повторить несобранное' }),
    ).toBeInTheDocument();
  });

  it('прерван ошибкой: причина берётся из ответа сервера', async () => {
    renderWith(
      catchupFixture('failed', { reason: 'не удалось записать собранное в хранилище' }),
      [FINISHED_RUN],
    );

    expect(await screen.findByText('Причина остановки')).toBeInTheDocument();
    expect(
      screen.getByText(/не удалось записать собранное в хранилище/),
    ).toBeInTheDocument();
  });

  it('прогонов ещё не было: единственный пустой экран', async () => {
    renderWith(catchupFixture('idle'), []);

    expect(await screen.findByText('Прогонов ещё не было')).toBeInTheDocument();
  });
});

describe('пауза автосбора', () => {
  it('начатый прогон не обрывает, но видна', async () => {
    server.use(http.get('*/api/market-data/settings', () => HttpResponse.json({ paused: true })));

    renderWith(catchupFixture('running'));

    expect(await screen.findByText('Автосбор на паузе')).toBeInTheDocument();
    // Прогон продолжается: кнопка остановки на месте, счётчики живы.
    expect(screen.getByRole('button', { name: 'Остановить прогон' })).toBeInTheDocument();
  });
});
