/**
 * Ручное обновление (T060).
 *
 * Проверяется поведение из US3: индикация выполнения, подтверждение,
 * защита от повторного запуска и понятное сообщение при неуспехе.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppProviders } from '@/app/providers';
import { RefreshNow } from '@/features/refresh-now/RefreshNow';
import { ToastHost } from '@/shared/ui/toast/ToastHost';

import { http, HttpResponse, server } from './msw/server';

const URL = '*/api/portfolio/refresh';

function renderButton(props: { inProgress?: boolean } = {}) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  });

  return render(
    <AppProviders client={client}>
      <ToastHost>
        <RefreshNow {...props} />
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

    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить сейчас' }));

    expect(await screen.findByText('Портфель обновлён')).toBeInTheDocument();
  });

  it('показывает индикацию выполнения и блокирует повторный запуск', async () => {
    let calls = 0;
    server.use(
      http.post(URL, async () => {
        calls += 1;
        await new Promise((resolve) => setTimeout(resolve, 50));
        return HttpResponse.json(okResponse());
      }),
    );

    renderButton();
    const button = screen.getByRole('button', { name: 'Обновить сейчас' });

    await userEvent.click(button);

    const busy = await screen.findByRole('button', { name: 'Обновляем…' });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute('aria-busy', 'true');

    await waitFor(() => expect(screen.getByRole('button')).toBeEnabled());
    // US3 AS3: повторное нажатие до завершения не создаёт второго запроса.
    expect(calls).toBe(1);
  });

  it('сообщает, что обновление уже выполнялось', async () => {
    server.use(http.post(URL, () => HttpResponse.json(okResponse(true))));

    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить сейчас' }));

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

    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить сейчас' }));

    const toast = await screen.findByText('Не удалось обновить портфель');
    // Технические детали ответа брокера пользователю не показываются (FR-028).
    expect(toast).not.toHaveTextContent('broker_unavailable');
  });

  it('различает обрыв связи с сервером', async () => {
    server.use(http.post(URL, () => HttpResponse.error()));

    renderButton();
    await userEvent.click(screen.getByRole('button', { name: 'Обновить сейчас' }));

    expect(await screen.findByText('Нет связи с сервером Financial AI')).toBeInTheDocument();
  });

  it('блокирует кнопку, пока синхронизация идёт на сервере', async () => {
    const called = vi.fn();
    server.use(
      http.post(URL, () => {
        called();
        return HttpResponse.json(okResponse());
      }),
    );

    renderButton({ inProgress: true });

    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(called).not.toHaveBeenCalled();
  });
});
