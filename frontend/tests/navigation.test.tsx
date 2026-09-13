/**
 * Общая оболочка и переходы между разделами (US3, FR-001–FR-007).
 *
 * Идущий сбор — процесс всего приложения: он виден из любого раздела и
 * переживает переход между ними.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { CatchupStateDto } from '@/entities/market-data';

import { catchupFixture } from './msw/market-data';
import { http, HttpResponse, server } from './msw/server';

function renderApp(path = '/') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });

  window.history.pushState(null, '', path);
  navigate(path === '/market-data' ? 'market-data' : 'portfolio');

  return render(
    <AppProviders client={client}>
      <AppShell />
    </AppProviders>,
  );
}

function withCatchup(state: CatchupStateDto) {
  server.use(http.get('*/api/market-data/catchup', () => HttpResponse.json(state)));
}

beforeEach(() => {
  window.history.pushState(null, '', '/');
});

describe('навигация между разделами', () => {
  it('показывает оба раздела и отмечает активный', async () => {
    renderApp('/');

    const nav = await screen.findByRole('navigation', { name: 'Разделы' });
    const portfolio = await screen.findByRole('link', { name: 'Портфель' });
    const marketData = await screen.findByRole('link', { name: 'Рыночные данные' });

    expect(nav).toContainElement(portfolio);
    expect(portfolio).toHaveAttribute('aria-current', 'page');
    expect(marketData).not.toHaveAttribute('aria-current');
  });

  it('переходит в рыночные данные и обратно, меняя адрес', async () => {
    renderApp('/');
    await screen.findByRole('heading', { name: 'Портфель' });

    await userEvent.click(screen.getByRole('link', { name: 'Рыночные данные' }));

    expect(await screen.findByRole('heading', { name: 'Рыночные данные' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/market-data');
    expect(screen.getByRole('link', { name: 'Рыночные данные' })).toHaveAttribute(
      'aria-current',
      'page',
    );

    await userEvent.click(screen.getByRole('link', { name: 'Портфель' }));

    expect(await screen.findByRole('heading', { name: 'Портфель' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/');
  });

  it('открывает раздел по прямому адресу', async () => {
    renderApp('/market-data');

    expect(await screen.findByRole('heading', { name: 'Рыночные данные' })).toBeInTheDocument();
  });

  it('переход «назад» возвращает прежний раздел', async () => {
    renderApp('/');
    await screen.findByRole('heading', { name: 'Портфель' });

    await userEvent.click(screen.getByRole('link', { name: 'Рыночные данные' }));
    await screen.findByRole('heading', { name: 'Рыночные данные' });

    window.history.back();

    expect(await screen.findByRole('heading', { name: 'Портфель' })).toBeInTheDocument();
  });

  it('состав действий шапки одинаков в обоих разделах', async () => {
    renderApp('/market-data');
    await screen.findByRole('heading', { name: 'Рыночные данные' });

    expect(screen.getByRole('button', { name: /Т-Банк/ })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Интервал автообновления портфеля' }),
    ).toBeInTheDocument();
    // В разделе рыночных данных то же действие перечитывает сводку (FR-018).
    expect(
      screen.getByRole('button', { name: 'Обновить сводку рыночных данных' }),
    ).toBeInTheDocument();
  });

  it('настройка интервала подписана как относящаяся к счёту', async () => {
    renderApp('/market-data');
    await screen.findByRole('heading', { name: 'Рыночные данные' });

    await userEvent.click(screen.getByRole('button', { name: 'Интервал автообновления портфеля' }));

    expect(await screen.findByLabelText('Автообновление портфеля')).toBeInTheDocument();
    expect(screen.getByText(/на рыночные данные не влияет/)).toBeInTheDocument();
  });
});

describe('баннер идущего сбора', () => {
  it('виден в обоих разделах, пока прогон идёт', async () => {
    withCatchup(catchupFixture('running'));
    renderApp('/');

    const rail = await screen.findByRole('status');
    // Процессов в баннере два, поэтому каждый назван своим именем: без этого
    // две строки читались бы как одна работа.
    expect(rail).toHaveTextContent('Догон данных продолжается');
    // Закрытые из запрошенных — те же числа, что и на странице (FR-007).
    expect(rail).toHaveTextContent('18 / 90');

    await userEvent.click(screen.getByRole('link', { name: 'Рыночные данные' }));
    await screen.findByRole('heading', { name: 'Рыночные данные' });

    expect(screen.getByRole('status')).toHaveTextContent('Догон данных продолжается');
  });

  it('во время остановки говорит о завершении текущей сессии', async () => {
    withCatchup(catchupFixture('stopping'));
    renderApp('/');

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Догон данных · завершаем текущую сессию',
    );
  });

  it('скрыт, когда прогон не идёт', async () => {
    withCatchup(catchupFixture('idle'));
    renderApp('/');

    await screen.findByRole('heading', { name: 'Портфель' });
    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });
  });

  it('ведёт в раздел рыночных данных', async () => {
    withCatchup(catchupFixture('running'));
    renderApp('/');

    const rail = await screen.findByRole('status');
    await userEvent.click(within(rail).getByRole('link'));

    expect(await screen.findByRole('heading', { name: 'Рыночные данные' })).toBeInTheDocument();
  });
});

describe('чтение состояния при входе в раздел', () => {
  it('перечитывает состояние прогона при переходе в рыночные данные', async () => {
    let calls = 0;
    server.use(
      http.get('*/api/market-data/catchup', () => {
        calls += 1;
        return HttpResponse.json(catchupFixture('idle'));
      }),
    );

    renderApp('/');
    await screen.findByRole('heading', { name: 'Портфель' });
    await waitFor(() => expect(calls).toBeGreaterThan(0));
    const before = calls;

    await userEvent.click(screen.getByRole('link', { name: 'Рыночные данные' }));
    await screen.findByRole('heading', { name: 'Рыночные данные' });

    // Опрос выключен, пока прогон неактивен, поэтому вход в раздел обязан
    // прочитать состояние сам (FR-035).
    await waitFor(() => expect(calls).toBeGreaterThan(before));
  });
});
