/**
 * Шесть состояний прогона (US2, FR-027–FR-037).
 *
 * У прогона один язык: состояние, счётчики, действие. Проверяется, что каждое
 * состояние показывает своё и что интерфейс ничего не додумывает за сервер.
 */

import { QueryClient } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { ToastHost } from '@/shared/ui/toast/ToastHost';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { CatchupStateDto } from '@/entities/market-data';

import { catchupFixture, coverageFixture } from './msw/market-data';
import { http, HttpResponse, server } from './msw/server';

function renderWith(state: CatchupStateDto) {
  server.use(http.get('*/api/market-data/catchup', () => HttpResponse.json(state)));

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

describe('состояния догона', () => {
  it('не запущен: предлагает настроить запуск', async () => {
    renderWith(catchupFixture('idle'));

    expect(await screen.findByText('Не запущен')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Настроить запуск' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Остановить' })).not.toBeInTheDocument();
  });

  it('идёт: показывает закрытые из запрошенных, остаток, незакрытые и текущую сессию', async () => {
    renderWith(catchupFixture('running'));

    expect(await screen.findByText('Идёт сбор')).toBeInTheDocument();
    expect(screen.getByText('из 90')).toBeInTheDocument();
    expect(screen.getByText('71')).toBeInTheDocument();
    expect(screen.getByText('Сейчас обрабатывается')).toBeInTheDocument();
    expect(screen.getByText('14.05.2026')).toBeInTheDocument();

    // Ход строится на закрытых из запрошенных; незакрывшиеся показаны
    // отдельно и в закрытые не входят (FR-030).
    const progress = screen.getByRole('progressbar', { name: 'Закрытые сессии' });
    expect(progress).toHaveAttribute('aria-valuenow', '18');
    expect(progress).toHaveAttribute('aria-valuemax', '90');
    expect(progress).toHaveAttribute(
      'aria-valuetext',
      'Закрыто 18 из 90; не закрыто 1; осталось обработать 71',
    );

    expect(screen.getByRole('button', { name: 'Остановить' })).toBeInTheDocument();
  });

  it('останавливается: ни повторной остановки, ни возобновления', async () => {
    renderWith(catchupFixture('stopping'));

    expect(await screen.findByText('Останавливается')).toBeInTheDocument();
    expect(screen.getByText('Остановка запрошена')).toBeInTheDocument();
    expect(screen.getByText('Завершаем сессию')).toBeInTheDocument();

    // FR-024: запрос уже доведёт текущую сессию до конца.
    expect(screen.queryByRole('button', { name: 'Остановить' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Продолжить догон' })).not.toBeInTheDocument();
  });

  it('остановлен: счётчики сохранены, доступно продолжение', async () => {
    renderWith(catchupFixture('stopped'));

    expect(await screen.findByText('Остановлен')).toBeInTheDocument();
    expect(screen.getByText('из 90')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Продолжить догон' })).toBeInTheDocument();
  });

  it('завершён с пропусками: без общей отметки успеха', async () => {
    renderWith(catchupFixture('finished', { closed: 89, failed: 1, remaining: 0 }));

    expect(await screen.findByText('Есть незакрытые сессии')).toBeInTheDocument();
    expect(screen.getByText('Диапазон завершён с пропусками')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Продолжить догон' })).toBeInTheDocument();
    expect(screen.queryByText('Догон завершён')).not.toBeInTheDocument();
  });

  it('завершён полностью: пропусков нет, продолжать нечего', async () => {
    renderWith(catchupFixture('finished', { closed: 90, failed: 0, remaining: 0 }));

    expect(await screen.findByText('Догон завершён')).toBeInTheDocument();
    expect(screen.getByText(/Наполненность проверяйте по сводке/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Продолжить догон' })).not.toBeInTheDocument();
  });

  it('прерван ошибкой: причина из ответа, прогресс сохранён', async () => {
    renderWith(
      catchupFixture('failed', {
        closed: 18,
        failed: 1,
        remaining: 71,
        reason: 'источник данных недоступен',
      }),
    );

    expect(await screen.findByText('Прерван ошибкой')).toBeInTheDocument();
    // Формулирует сервер, а не интерфейс (FR-034).
    expect(screen.getByText('источник данных недоступен')).toBeInTheDocument();
    expect(screen.getByText('из 90')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Продолжить догон' })).toBeInTheDocument();
  });

  it('останавливает по одному действию и не переходит в «остановлен» сам', async () => {
    let stopped = false;
    server.use(
      http.get('*/api/market-data/catchup', () =>
        HttpResponse.json(catchupFixture(stopped ? 'stopping' : 'running')),
      ),
      http.delete('*/api/market-data/catchup', () => {
        stopped = true;
        return HttpResponse.json({ status: 'stopping', current: '2026-05-14' });
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchInterval: false } },
    });
    window.history.pushState(null, '', '/market-data');
    navigate('market-data');
    render(
      <AppProviders client={client}>
        <AppShell />
      </AppProviders>,
    );

    await screen.findByText('Идёт сбор');
    // Одно нажатие, без диалога подтверждения (FR-023).
    await userEvent.click(screen.getByRole('button', { name: 'Остановить' }));

    // Переход только по состоянию с сервера: «Остановлен» по факту нажатия
    // не появляется (FR-032).
    expect(await screen.findByText('Останавливается')).toBeInTheDocument();
    expect(screen.queryByText('Остановлен')).not.toBeInTheDocument();
  });

  it('ответ idle отменяет ранее показанный идущий прогон', async () => {
    let restarted = false;
    server.use(
      http.get('*/api/market-data/catchup', () =>
        HttpResponse.json(catchupFixture(restarted ? 'idle' : 'running')),
      ),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchInterval: false } },
    });
    window.history.pushState(null, '', '/market-data');
    navigate('market-data');
    const view = render(
      <AppProviders client={client}>
        <AppShell />
      </AppProviders>,
    );

    await screen.findByText('Идёт сбор');

    // Сборщик перезапустили: состояние прогона живёт в процессе и исчезает
    // вместе с ним (FR-035).
    restarted = true;
    await client.invalidateQueries();
    view.rerender(
      <AppProviders client={client}>
        <AppShell />
      </AppProviders>,
    );

    expect(await screen.findByText('Не запущен')).toBeInTheDocument();
    expect(screen.queryByText('Идёт сбор')).not.toBeInTheDocument();
  });
});

describe('кнопка запуска в заголовке раздела', () => {
  it('в покое предлагает запуск, во время работы — параметры', async () => {
    renderWith(catchupFixture('idle'));

    const idle = await screen.findByRole('button', { name: 'Запустить догон' });
    expect(idle).toHaveClass('primary-button');

    cleanup();
    renderWith(catchupFixture('running'));

    // Прогон уже идёт: предлагать «запустить» было бы неверно.
    const busy = await screen.findByRole('button', { name: 'Параметры догона' });
    expect(busy).toHaveClass('secondary-button');
    expect(screen.queryByRole('button', { name: 'Запустить догон' })).not.toBeInTheDocument();
  });

  it('открывает ту же форму, что и кнопка в секции', async () => {
    renderWith(catchupFixture('idle'));

    await userEvent.click(await screen.findByRole('button', { name: 'Запустить догон' }));

    expect(await screen.findByRole('dialog', { name: 'Запустить догон' })).toBeInTheDocument();
  });
});

describe('продолжение догона', () => {
  it('запускается сразу, без формы, с группами прошлого прогона', async () => {
    let body: unknown;
    server.use(
      http.get('*/api/market-data/catchup', () =>
        HttpResponse.json(catchupFixture('stopped', { groups: ['quotes', 'aggregates'] })),
      ),
      http.post('*/api/market-data/catchup', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({
          status: 'running',
          groups: ['quotes', 'aggregates'],
          clamped: false,
          requested_sessions: 71,
        });
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchInterval: false } },
    });
    window.history.pushState(null, '', '/market-data');
    navigate('market-data');
    render(
      <AppProviders client={client}>
        <ToastHost>
          <AppShell />
        </ToastHost>
      </AppProviders>,
    );

    await userEvent.click(await screen.findByRole('button', { name: 'Продолжить догон' }));

    // Формы нет: продолжение — это тот же запуск, а пропуски сервер
    // пересчитывает сам (FR-025).
    expect(screen.queryByRole('dialog', { name: 'Запустить догон' })).not.toBeInTheDocument();
    await waitFor(() => expect(body).toEqual({ groups: ['quotes', 'aggregates'] }));
    expect(await screen.findByText(/Продолжение запущено/)).toBeInTheDocument();
  });
});

describe('сводка после прогона', () => {
  it('перечитывается, когда прогон завершился', async () => {
    let active = true;
    let coverageCalls = 0;

    server.use(
      http.get('*/api/market-data/coverage', () => {
        coverageCalls += 1;
        return HttpResponse.json(coverageFixture());
      }),
      http.get('*/api/market-data/catchup', () =>
        HttpResponse.json(
          active
            ? catchupFixture('running')
            : catchupFixture('finished', { closed: 90, failed: 0, remaining: 0 }),
        ),
      ),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchInterval: false } },
    });
    window.history.pushState(null, '', '/market-data');
    navigate('market-data');
    render(
      <AppProviders client={client}>
        <AppShell />
      </AppProviders>,
    );

    await screen.findByText('Идёт сбор');
    const before = coverageCalls;

    active = false;
    await client.invalidateQueries({ queryKey: ['market-data', 'catchup'] });
    await screen.findByText('Догон завершён');

    // Числа берутся из настоящего чтения с сервера, а не выводятся из
    // статуса прогона (FR-037).
    await waitFor(() => expect(coverageCalls).toBeGreaterThan(before));
  });
});
