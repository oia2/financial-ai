/**
 * История прогонов и просмотр прогона (US4, FR-039, FR-040, FR-048–FR-052).
 *
 * Проверяются утверждения, а не разметка: отбор и страницы идут на сервер,
 * скоры эмулятора помечены вымышленными, удалённый набор объясняется и
 * запрещает повтор, устаревший вход различает последнюю дату и историческую.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { RunDetailDto, RunRowDto } from '@/entities/daily-ml';

import { historyFixture, runDetailFixture, runFixture, statusFixture } from './msw/daily-ml';
import { http, HttpResponse, server } from './msw/server';

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });

  window.history.pushState(null, '', '/daily-ml');
  navigate('daily-ml');

  return render(
    <AppProviders client={client}>
      <AppShell />
    </AppProviders>,
  );
}

/** Список с отбором и страницами, как отвечает сервер. */
function serveHistory(rows: RunRowDto[], onQuery?: (params: URLSearchParams) => void) {
  server.use(
    http.get('*/api/daily-ml/runs', ({ request }) => {
      const params = new URL(request.url).searchParams;
      onQuery?.(params);

      const status = params.get('status');
      const limit = Number(params.get('limit') ?? 10);
      const offset = Number(params.get('offset') ?? 0);
      const selected = status === null ? rows : rows.filter((row) => row.status === status);

      return HttpResponse.json(
        historyFixture(selected.slice(offset, offset + limit), selected.length),
      );
    }),
  );
}

function serveDetail(detail: RunDetailDto) {
  server.use(http.get('*/api/daily-ml/runs/:id', () => HttpResponse.json(detail)));
}

describe('история прогонов', () => {
  it('пустая история объясняет, что пропущенные даты долгом не считаются', async () => {
    serveHistory([]);
    renderPage();

    expect(await screen.findByText('Прогонов ещё не было')).toBeInTheDocument();
    expect(
      screen.getByText(/Пропущенные исторические даты долгом не считаются/),
    ).toBeInTheDocument();
  });

  it('отбор по состоянию уходит на сервер и возвращает к первой странице', async () => {
    const queries: URLSearchParams[] = [];
    serveHistory(
      [
        runFixture({ id: 143 }),
        runFixture({ id: 142, status: 'failed', error_message: 'звено не ответило' }),
      ],
      (params) => queries.push(params),
    );
    renderPage();

    await screen.findByRole('table', { name: undefined });
    await userEvent.selectOptions(screen.getByLabelText('Статус'), 'failed');

    expect(await screen.findByText('звено не ответило')).toBeInTheDocument();
    const last = queries.at(-1)!;
    expect(last.get('status')).toBe('failed');
    expect(last.get('offset')).toBe('0');
  });

  it('страницы считаются по общему числу прогонов, а не по показанным строкам', async () => {
    const rows = Array.from({ length: 23 }, (_, index) =>
      runFixture({ id: 200 - index, asof_date: `2026-08-${String(index + 1).padStart(2, '0')}` }),
    );
    serveHistory(rows);
    renderPage();

    expect(await screen.findByLabelText('Страница 1 из 3')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }));
    expect(await screen.findByLabelText('Страница 2 из 3')).toBeInTheDocument();
  });

  it('повтор предлагается только у отказавшего прогона', async () => {
    serveHistory([
      runFixture({ id: 143 }),
      runFixture({ id: 142, status: 'failed', duration_seconds: null }),
    ]);
    renderPage();

    const table = await screen.findByRole('table');
    const retries = within(table).getAllByRole('button', { name: 'Повторить' });
    expect(retries).toHaveLength(1);
  });
});

describe('просмотр прогона', () => {
  it('показывает вход, исход и результат', async () => {
    serveHistory([runFixture()]);
    serveDetail(runDetailFixture());
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: /Открыть прогон за/ }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('EQ_AST_SBER')).toBeInTheDocument();
    // Скор показывается строкой из ответа: разбор в число изменил бы
    // последний разряд, а он участвует в порядке.
    expect(within(drawer).getByText('0,996528')).toBeInTheDocument();

    await userEvent.click(within(drawer).getByText('Входные данные и сведения о прогоне'));
    expect(within(drawer).getByText('sha256:9f2c00')).toBeInTheDocument();
    expect(within(drawer).getByText('Обязательный вход объявлен полным')).toBeInTheDocument();
  });

  it('скоры эмулятора помечены вымышленными прямо на результате', async () => {
    serveHistory([runFixture()]);
    serveDetail(runDetailFixture({ emulated: true }));
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: /Открыть прогон за/ }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('Скоры вымышлены эмулятором')).toBeInTheDocument();
    // FR-050: скор задаёт порядок внутри даты и доходности не означает.
    expect(
      within(drawer).getByText(/Между датами он не\s+сравнивается и доходности не означает/),
    ).toBeInTheDocument();
  });

  it('удалённый набор объясняется, и повтор по нему невозможен', async () => {
    serveHistory([runFixture({ status: 'failed' })]);
    serveDetail(
      runDetailFixture({
        status: 'failed',
        error_message: 'звено ранжирования не ответило',
        input: {
          dataset_digest: 'sha256:9f2c00',
          dataset_ref: 'file:///datasets/2026-09-11-9f2c00',
          dataset_available: false,
          window_from: '2025-06-11',
          window_till: '2026-09-11',
          complete: true,
        },
      } as Partial<RunDetailDto>),
    );
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: /Открыть прогон за/ }));

    const drawer = await screen.findByRole('dialog');
    expect(
      within(drawer).getByText('Входной набор удалён после срока хранения'),
    ).toBeInTheDocument();
    // Кнопка остаётся видимой и выключенной: исчезнувшая не объяснила бы, почему.
    expect(within(drawer).getByRole('button', { name: 'Повторить' })).toBeDisabled();
  });

  it('устаревший вход у исторической даты не обещает пересчёта', async () => {
    server.use(
      http.get('*/api/daily-ml/status', () =>
        HttpResponse.json(statusFixture({ latest_data_ready: '2026-09-11' })),
      ),
    );
    serveHistory([runFixture({ id: 100, asof_date: '2026-06-02' })]);
    serveDetail(runDetailFixture({ id: 100, asof_date: '2026-06-02', stale: true }));
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: /Открыть прогон за/ }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('После прогона вход изменился')).toBeInTheDocument();
    expect(
      within(drawer).getByText('Это историческая дата. Автоматического пересчёта для неё нет.'),
    ).toBeInTheDocument();
  });

  it('прогон без результата говорит, что результата нет, а не показывает пустую таблицу', async () => {
    serveHistory([runFixture({ status: 'queued', started_at: null, duration_seconds: null })]);
    serveDetail(
      runDetailFixture({
        status: 'queued',
        started_at: null,
        finished_at: null,
        duration_seconds: null,
        items: [],
      } as Partial<RunDetailDto>),
    );
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: /Открыть прогон за/ }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('Прогон ожидает обработки')).toBeInTheDocument();
    expect(within(drawer).queryByRole('table')).not.toBeInTheDocument();
  });
});
