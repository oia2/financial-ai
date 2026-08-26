/**
 * Настройка интервала автообновления (T046).
 *
 * Проверяется поведение из US2 AS3/AS4: допустимое значение сохраняется,
 * недопустимое отклоняется с объяснением диапазона, а прежний интервал
 * продолжает действовать.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppProviders } from '@/app/providers';
import { RefreshIntervalSetting } from '@/features/refresh-interval-setting/RefreshIntervalSetting';

import { http, HttpResponse, server } from './msw/server';

const URL = '*/api/settings/refresh-interval';

function renderSetting() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });

  return render(
    <AppProviders client={client}>
      <RefreshIntervalSetting />
    </AppProviders>,
  );
}

function settingsResponse(interval: number) {
  return {
    interval_seconds: interval,
    min_seconds: 15,
    max_seconds: 3600,
    default_seconds: 60,
  };
}

describe('настройка интервала', () => {
  it('показывает действующее значение и допустимый диапазон', async () => {
    renderSetting();

    expect(await screen.findByText(/Целое число от 15 до 3600 секунд/)).toBeInTheDocument();
    expect(screen.getByText('60 с')).toBeInTheDocument();
    expect(await screen.findByLabelText(/Интервал автообновления/)).toHaveValue(60);
  });

  it('сохраняет допустимое значение', async () => {
    const received = vi.fn();
    server.use(
      http.put(URL, async ({ request }) => {
        const body = (await request.json()) as { interval_seconds: number };
        received(body.interval_seconds);
        return HttpResponse.json(settingsResponse(body.interval_seconds));
      }),
    );

    renderSetting();
    const input = await screen.findByLabelText(/Интервал автообновления/);

    await userEvent.clear(input);
    await userEvent.type(input, '120');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText('120 с')).toBeInTheDocument();
    expect(received).toHaveBeenCalledWith(120);
  });

  it('отклоняет значение вне диапазона и сохраняет прежний интервал', async () => {
    server.use(
      http.put(URL, () =>
        HttpResponse.json(
          { detail: { code: 'interval_out_of_range', min_seconds: 15, max_seconds: 3600 } },
          { status: 422 },
        ),
      ),
    );

    renderSetting();
    const input = await screen.findByLabelText(/Интервал автообновления/);

    await userEvent.clear(input);
    await userEvent.type(input, '5');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/вне допустимого диапазона/i);
    expect(alert).toHaveTextContent(/Прежний интервал 60 с продолжает действовать/);
    // Действующее значение не изменилось.
    expect(screen.getByText('60 с')).toBeInTheDocument();
  });

  it('отклоняет нецелое значение без обращения к серверу', async () => {
    const called = vi.fn();
    server.use(
      http.put(URL, () => {
        called();
        return HttpResponse.json(settingsResponse(60));
      }),
    );

    renderSetting();
    const input = await screen.findByLabelText(/Интервал автообновления/);

    await userEvent.clear(input);
    await userEvent.type(input, '60.5');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/целое число секунд/i);
    expect(called).not.toHaveBeenCalled();
  });

  it('помечает поле как некорректное при ошибке', async () => {
    renderSetting();
    const input = await screen.findByLabelText(/Интервал автообновления/);

    await userEvent.clear(input);
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(input).toHaveAttribute('aria-invalid', 'true');
  });

  it('снимает ошибку при новом вводе', async () => {
    renderSetting();
    const input = await screen.findByLabelText(/Интервал автообновления/);

    await userEvent.clear(input);
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();

    await userEvent.type(input, '90');

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
