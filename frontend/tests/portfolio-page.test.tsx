/**
 * Раздел «Портфель»: отображение состояния счёта (T028).
 *
 * Проверяется то, что видит пользователь: сводные показатели, таблица
 * позиций, возраст данных и время последней синхронизации.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppProviders } from '@/app/providers';
import { PortfolioPage } from '@/pages/portfolio/PortfolioPage';
import type { PortfolioDto } from '@/entities/portfolio';

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

describe('состояние счёта', () => {
  it('показывает сводные показатели капитала', async () => {
    renderPage();

    expect(await screen.findByText(/402\s?609,00/)).toBeInTheDocument();
    expect(screen.getByText(/40\s?545,00/)).toBeInTheDocument();
    expect(screen.getByText(/\+4\s?590,00/)).toBeInTheDocument();
    expect(screen.getByText(/\+1,29% к средней стоимости/)).toBeInTheDocument();
  });

  it('показывает долю денежных средств', async () => {
    renderPage();

    expect(await screen.findByText(/10,1% портфеля/)).toBeInTheDocument();
  });

  it('показывает таблицу позиций', async () => {
    renderPage();

    expect(await screen.findByRole('table', { name: 'Позиции' })).toBeInTheDocument();
    expect(screen.getByText('SBER')).toBeInTheDocument();
    expect(screen.getByText('Сбербанк')).toBeInTheDocument();
    expect(screen.getByText(/362\s?064,00/)).toBeInTheDocument();
  });

  it('отображает позицию без тикера и названия по идентификатору', async () => {
    renderPage();

    // Отсутствие названия не блокирует отображение остальных данных.
    expect(await screen.findByText('uid-unknown')).toBeInTheDocument();
  });

  it('показывает возраст данных и время последней синхронизации', async () => {
    renderPage();

    expect(await screen.findByText('Сейчас')).toBeInTheDocument();
    expect(screen.getByText(/Обновлено/)).toBeInTheDocument();
  });

  it('показывает маскированный номер договора и не показывает полный', async () => {
    renderPage();

    // Маскированный номер показан и в шапке, и в меню счёта.
    expect((await screen.findAllByText(/•• 4821/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/2000124821/)).not.toBeInTheDocument();
  });
});

describe('граничные состояния', () => {
  it('показывает состояние загрузки до ответа', () => {
    renderPage();

    expect(screen.getByText(/Обновляем данные портфеля/)).toBeInTheDocument();
  });

  it('показывает «брокер не подключён», когда доступ не сконфигурирован', async () => {
    respondWith(
      portfolioFixture({
        broker: { status: 'not_configured', account: null },
        snapshot: null,
      }),
    );

    renderPage();

    // Текст показан в строке подключения и в панели состояния.
    expect((await screen.findAllByText(/Доступ к Т-Банк не сконфигурирован/)).length).toBe(2);
    // Кнопки подключения нет: доступ задаётся конфигурацией сервера.
    expect(screen.queryByRole('button', { name: /Подключить/ })).not.toBeInTheDocument();
  });

  it('не показывает нули как фактические данные, когда снимка ещё нет', async () => {
    respondWith(
      portfolioFixture({
        snapshot: null,
        sync: {
          status: 'failed',
          last_success_at: null,
          last_attempt_at: '2026-08-26T11:32:18Z',
          failure_reason_code: 'broker_unavailable',
          is_stale: false,
          stale_after_seconds: 180,
          refresh_interval_seconds: 60,
          in_progress: false,
        },
      }),
    );

    renderPage();

    expect(await screen.findByText(/Данных о счёте пока нет/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('показывает пустой портфель без ложных процентов', async () => {
    respondWith(
      portfolioFixture({
        snapshot: {
          captured_at: '2026-08-26T11:32:18Z',
          age_seconds: 5,
          total_value: '0',
          cash: '0',
          cash_share: '0',
          unrealized_pnl: '0',
          unrealized_pnl_percent: null,
          positions_count: 0,
          positions: [],
        },
      }),
    );

    renderPage();

    expect(await screen.findByText(/В портфеле пока нет позиций/)).toBeInTheDocument();
    expect(screen.getByText('Процент не определён')).toBeInTheDocument();
  });
});

describe('точность значений', () => {
  it('не теряет разряды на суммах за пределами точности double', async () => {
    const fixture = portfolioFixture();
    respondWith(
      portfolioFixture({
        snapshot: {
          ...fixture.snapshot!,
          total_value: '9007199254740993.010000000',
        },
      }),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/9\s?007\s?199\s?254\s?740\s?993,01/)).toBeInTheDocument(),
    );
  });
});

describe('цветовая семантика P&L', () => {
  it('положительный P&L окрашен в цвет прибыли', async () => {
    renderPage();

    const value = await screen.findByText(/\+4\s?590,00/);
    expect(value).toHaveClass('fact-value', 'positive');
  });

  it('отрицательный P&L окрашен в цвет убытка', async () => {
    const fixture = portfolioFixture();
    respondWith(
      portfolioFixture({
        snapshot: {
          ...fixture.snapshot!,
          unrealized_pnl: '-4590.000000000',
          unrealized_pnl_percent: '-0.0129',
        },
      }),
    );

    renderPage();

    const value = await screen.findByText(/−4\s?590,00/);
    expect(value).toHaveClass('fact-value', 'negative');
  });
});
