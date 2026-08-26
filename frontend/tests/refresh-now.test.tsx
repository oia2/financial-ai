/**
 * Ручное обновление (T060).
 *
 * В утверждённом дизайне действие живёт в шапке — иконкой и пунктом меню
 * счёта, — поэтому проверяется именно оно, а не отдельная кнопка.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppProviders } from '@/app/providers';
import { ToastHost } from '@/shared/ui/toast/ToastHost';
import { AppHeader } from '@/widgets/app-header/AppHeader';

import { portfolioFixture } from './msw/fixtures';
import { http, HttpResponse, server } from './msw/server';

const URL = '*/api/portfolio/refresh';

function renderHeader(overrides: Parameters<typeof portfolioFixture>[0] = {}) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  });

  return render(
    <AppProviders client={client}>
      <ToastHost>
        <AppHeader data={portfolioFixture(overrides)} />
      </ToastHost>
    </AppProviders>,
  );
}

function okResponse(deduplicated = false) {
  return {
    status: 'ok',
    deduplicated,
    captured_at: '2026-08-26T11:35:02Z',
    failure_reason_code: null,
  };
}

describe('ручное обновление', () => {
  it('подтверждает успешное обновление', async () => {
    server.use(http.post(URL, () => HttpResponse.json(okResponse())));

    renderHeader();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить данные' }));

    expect(await screen.findByText('Портфель обновлён')).toBeInTheDocument();
  });

  it('блокирует повторный запуск до завершения', async () => {
    let calls = 0;
    server.use(
      http.post(URL, async () => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 50));
        return HttpResponse.json(okResponse());
      }),
    );

    renderHeader();
    const button = screen.getByRole('button', { name: 'Обновить данные' });

    await userEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');

    await waitFor(() => expect(button).toBeEnabled());
    // US3 AS3: второй запрос к брокеру не создаётся.
    expect(calls).toBe(1);
  });

  it('сообщает, что обновление уже выполнялось', async () => {
    server.use(http.post(URL, () => HttpResponse.json(okResponse(true))));

    renderHeader();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить данные' }));

    expect(await screen.findByText('Обновление уже выполнялось')).toBeInTheDocument();
  });

  it('сообщает о сбое брокера понятным языком', async () => {
    server.use(
      http.post(URL, () =>
        HttpResponse.json({
          status: 'failed',
          deduplicated: false,
          captured_at: null,
          failure_reason_code: 'broker_unavailable',
        }),
      ),
    );

    renderHeader();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить данные' }));

    const toast = await screen.findByText('Не удалось обновить портфель');
    // Технические детали ответа брокера пользователю не показываются (FR-028).
    expect(toast).not.toHaveTextContent('broker_unavailable');
  });

  it('различает обрыв связи с сервером', async () => {
    server.use(http.post(URL, () => HttpResponse.error()));

    renderHeader();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить данные' }));

    expect(await screen.findByText('Нет связи с сервером Financial AI')).toBeInTheDocument();
  });

  it('блокирует действие, пока синхронизация идёт на сервере', async () => {
    const called = vi.fn();
    server.use(
      http.post(URL, () => {
        called();
        return HttpResponse.json(okResponse());
      }),
    );

    const fixture = portfolioFixture();
    renderHeader({ sync: { ...fixture.sync, in_progress: true } });

    const button = screen.getByRole('button', { name: 'Обновить данные' });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(called).not.toHaveBeenCalled();
  });

  it('повторяет действие в меню счёта', async () => {
    server.use(http.post(URL, () => HttpResponse.json(okResponse())));

    renderHeader();
    await userEvent.click(screen.getByRole('button', { name: /Т-Банк/ }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Обновить данные' }));

    expect(await screen.findByText('Портфель обновлён')).toBeInTheDocument();
  });
});
