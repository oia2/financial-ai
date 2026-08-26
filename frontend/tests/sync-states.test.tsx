/**
 * Состояния несвежести данных (T069, FR-037, FR-038, SC-011).
 *
 * Три причины должны быть однозначно различимы: устаревание, сбой T-Bank API
 * и обрыв связи с сервером Financial AI.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppProviders } from '@/app/providers';
import { selectPortfolioState, type PortfolioDto } from '@/entities/portfolio';
import { PortfolioPage } from '@/pages/portfolio/PortfolioPage';
import { ServerUnreachableError } from '@/shared/api/client';

import { portfolioFixture } from './msw/fixtures';
import { http, HttpResponse, server } from './msw/server';

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });

  return render(
    <AppProviders client={client}>
      <PortfolioPage />
    </AppProviders>,
  );
}

function respondWith(payload: PortfolioDto) {
  server.use(http.get('*/api/portfolio', () => HttpResponse.json(payload)));
}

const staleFixture = portfolioFixture({
  sync: {
    status: 'ok',
    last_success_at: '2026-08-26T11:04:12Z',
    last_attempt_at: '2026-08-26T11:04:12Z',
    failure_reason_code: null,
    is_stale: true,
    stale_after_seconds: 180,
    refresh_interval_seconds: 60,
    in_progress: false,
  },
});

const brokerFailureFixture = portfolioFixture({
  sync: {
    status: 'failed',
    last_success_at: '2026-08-26T11:04:12Z',
    last_attempt_at: '2026-08-26T11:32:18Z',
    failure_reason_code: 'broker_unavailable',
    is_stale: true,
    stale_after_seconds: 180,
    refresh_interval_seconds: 60,
    in_progress: false,
  },
});

describe('различимость причин', () => {
  it('показывает устаревание данных', async () => {
    respondWith(staleFixture);
    renderPage();

    expect(await screen.findByText('Данные временно устарели')).toBeInTheDocument();
    // Данные остаются на экране.
    expect(screen.getByRole('table', { name: 'Позиции' })).toBeInTheDocument();
  });

  it('показывает сбой брокера отдельным сообщением', async () => {
    respondWith(brokerFailureFixture);
    renderPage();

    expect(await screen.findByText('Не удалось обновить портфель')).toBeInTheDocument();
    expect(screen.getByText(/T-Bank API не ответил/)).toBeInTheDocument();
    // Время последней успешной синхронизации указано (FR-026).
    expect(screen.getByText(/последнее успешно синхронизированное состояние/i)).toBeInTheDocument();
    expect(screen.queryByText('Нет связи с сервером Financial AI')).not.toBeInTheDocument();
  });

  it('показывает обрыв связи с сервером отдельным сообщением', async () => {
    server.use(http.get('*/api/portfolio', () => HttpResponse.error()));
    renderPage();

    expect(await screen.findByText('Нет связи с сервером Financial AI')).toBeInTheDocument();
    expect(screen.getByText(/автоматически пытается восстановить связь/)).toBeInTheDocument();
    // Сообщение о сбое брокера при этом не показывается.
    expect(screen.queryByText('Не удалось обновить портфель')).not.toBeInTheDocument();
  });

  it('сохраняет последние известные данные при обрыве связи', async () => {
    let failing = false;
    server.use(
      http.get('*/api/portfolio', () =>
        failing ? HttpResponse.error() : HttpResponse.json(portfolioFixture()),
      ),
      http.post('*/api/portfolio/refresh', () =>
        HttpResponse.json({
          status: 'ok',
          deduplicated: false,
          captured_at: '2026-08-26T11:35:02Z',
          failure_reason_code: null,
        }),
      ),
    );

    renderPage();
    expect(await screen.findByText('SBER')).toBeInTheDocument();

    // Связь пропадает между обновлениями: следующий запрос уже не дойдёт.
    failing = true;
    await userEvent.click(screen.getByRole('button', { name: 'Обновить данные' }));

    expect(await screen.findByText('Нет связи с сервером Financial AI')).toBeInTheDocument();
    // Данные помечены как последние известные, а не стёрты.
    expect(screen.getByText('SBER')).toBeInTheDocument();
  });

  it('снимает предупреждение после восстановления связи', async () => {
    let failing = true;
    server.use(
      http.get('*/api/portfolio', () =>
        failing ? HttpResponse.error() : HttpResponse.json(portfolioFixture()),
      ),
    );

    renderPage();
    expect(await screen.findByText('Нет связи с сервером Financial AI')).toBeInTheDocument();

    failing = false;
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));

    // FR-039: без перезагрузки страницы.
    await waitFor(() =>
      expect(screen.queryByText('Нет связи с сервером Financial AI')).not.toBeInTheDocument(),
    );
    expect(screen.getByText('SBER')).toBeInTheDocument();
  });

  it('объясняет ограничение частоты запросов', async () => {
    respondWith(
      portfolioFixture({
        sync: { ...brokerFailureFixture.sync, failure_reason_code: 'rate_limited' },
      }),
    );
    renderPage();

    expect(await screen.findByText(/ограничил частоту запросов/)).toBeInTheDocument();
  });
});

describe('выбор состояния', () => {
  it('обрыв связи определяется по отсутствию ответа, а не по полю в нём', () => {
    const state = selectPortfolioState(undefined, new ServerUnreachableError(), false);

    expect(state).toBe('server-offline');
  });

  it('сбой брокера остаётся сбоем брокера при полученном ответе', () => {
    expect(selectPortfolioState(brokerFailureFixture, null, false)).toBe('sync-error');
  });

  it('несконфигурированный доступ важнее прочих состояний', () => {
    const notConfigured = portfolioFixture({
      broker: { status: 'not_configured', account: null },
      snapshot: null,
    });

    expect(selectPortfolioState(notConfigured, null, false)).toBe('no-broker');
  });

  it('устаревание распознаётся по флагу сервера', () => {
    expect(selectPortfolioState(staleFixture, null, false)).toBe('stale');
  });
});
