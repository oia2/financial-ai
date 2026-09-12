/**
 * Пограничные состояния раздела (US4, FR-038–FR-043).
 *
 * Десять случаев с листа проверки дизайна. Проверяется главное: ни одна
 * причина не выдаётся за другую. Интерфейс, подменяющий причину, дороже
 * отсутствующего — он приводит к неверным действиям ценой сотен обращений к
 * бирже.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';

import { anomalyCoverageFixture, catchupFixture, coverageFixture } from './msw/market-data';
import { http, HttpResponse, server } from './msw/server';

function renderMarketData() {
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

function refuseStart(status: number, code: string) {
  server.use(
    http.post('*/api/market-data/catchup', () =>
      HttpResponse.json({ detail: { code, message: 'отказ' } }, { status }),
    ),
  );
}

async function submitLaunch() {
  await userEvent.click(await screen.findByRole('button', { name: 'Настроить запуск' }));
  const form = await screen.findByRole('dialog', { name: 'Запустить догон' });
  await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));
  return form;
}

describe('пустое хранилище и полнота', () => {
  it('пустое хранилище: объяснение вместо аварии, запуск недоступен', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          { detail: { code: 'calendar_empty', message: 'календарь пуст' } },
          { status: 422 },
        ),
      ),
    );

    renderMarketData();

    expect(await screen.findByText('Хранилище пока пусто')).toBeInTheDocument();

    // Первичная загрузка остаётся вне интерфейса: запускать нечего и нечем
    // (FR-026). Кнопка в заголовке недоступна и говорит, чего не хватает.
    expect(screen.queryByRole('button', { name: 'Настроить запуск' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Нужна первичная загрузка' })).toBeDisabled();
    expect(
      within(
        screen.getByRole('heading', { name: 'Догон истории' }).closest('div') as HTMLElement,
      ).getByText('Нужна первичная загрузка'),
    ).toBeInTheDocument();
  });

  it('догонять нечего: спокойное подтверждение, новый прогон не запущен', async () => {
    server.use(
      http.post('*/api/market-data/catchup', () =>
        HttpResponse.json({
          status: 'idle',
          groups: [],
          requested_sessions: 0,
          reason: 'пропущенных сессий нет',
        }),
      ),
    );

    renderMarketData();
    await submitLaunch();

    expect(await screen.findByText('Пропущенных сессий нет')).toBeInTheDocument();
    // Это не отказ: слова об ошибке здесь быть не должно.
    expect(screen.queryByText(/Не удалось/)).not.toBeInTheDocument();

    // Новый прогон не предлагается, пока сервер говорит «нечего» (FR-038).
    expect(screen.getByText('Догонять нечего')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Настроить запуск' })).not.toBeInTheDocument();
  });

  it('полное покрытие не выдаётся за полноту значений', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => HttpResponse.json(anomalyCoverageFixture())),
    );

    renderMarketData();

    // Доля значений остаётся отдельным показателем даже при покрытии 100%.
    const positions = (await screen.findByRole('table')).querySelector('.anomaly-row');
    expect(positions).not.toBeNull();
    expect(within(positions as HTMLElement).getByText('100,0%')).toBeInTheDocument();
    expect(within(positions as HTMLElement).getByText('0,0%')).toBeInTheDocument();
  });
});

describe('отказы запуска', () => {
  it('повторный запуск отклонён: счётчики идущего прогона сохранены', async () => {
    // Прогон запустили из другой вкладки: на этом экране ещё «не запущен».
    let running = false;
    server.use(
      http.get('*/api/market-data/catchup', () =>
        HttpResponse.json(catchupFixture(running ? 'running' : 'idle')),
      ),
      http.post('*/api/market-data/catchup', () => {
        running = true;
        return HttpResponse.json(
          { detail: { code: 'catchup_already_running', message: 'догон уже выполняется' } },
          { status: 409 },
        );
      }),
    );

    renderMarketData();
    await submitLaunch();

    expect(await screen.findByText(/Догон уже выполняется/)).toBeInTheDocument();
    // Счётчики и состояние существующего прогона на экране, а не затёрты
    // отказом (FR-039).
    expect(await screen.findByText('Идёт сбор')).toBeInTheDocument();
    expect(screen.getByText('из 90')).toBeInTheDocument();
  });

  it('неизвестная группа: отказ объясняется как устаревший запрос', async () => {
    refuseStart(422, 'unknown_group');

    renderMarketData();
    const form = await submitLaunch();

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'Неизвестная группа. Запуск отклонён. Выберите группы из списка.',
    );
  });

  it('пустое хранилище при запуске: обращение к администратору', async () => {
    refuseStart(422, 'backfill_required');

    renderMarketData();
    const form = await submitLaunch();

    expect(await within(form).findByRole('alert')).toHaveTextContent(/первичная загрузка/);
    expect(within(form).getByRole('alert')).toHaveTextContent(/администратору/);
  });

  it('сборщик недоступен: не выдаётся за отказ операции', async () => {
    refuseStart(503, 'worker_unavailable');

    renderMarketData();
    const form = await submitLaunch();

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'Сборщик данных недоступен. Команда не выполнена.',
    );
  });

  it('нет связи с сервером отличается от отказа сборщика', async () => {
    server.use(http.post('*/api/market-data/catchup', () => HttpResponse.error()));

    renderMarketData();
    const form = await submitLaunch();

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'Нет связи с сервером Financial AI.',
    );
  });

  it('в сообщениях нет технических подробностей и внутренних адресов', async () => {
    server.use(
      http.post('*/api/market-data/catchup', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'worker_unavailable',
              message: 'ConnectError to http://backend-worker:8000/internal/catchup',
            },
          },
          { status: 503 },
        ),
      ),
    );

    renderMarketData();
    const form = await submitLaunch();

    const alert = await within(form).findByRole('alert');
    expect(alert.textContent).not.toMatch(/backend-worker|internal|ConnectError/);
  });
});

describe('сужение диапазона', () => {
  it('показывает введённый и принятый диапазоны и не убирает сообщение', async () => {
    server.use(
      http.post('*/api/market-data/catchup', () =>
        HttpResponse.json({
          status: 'running',
          groups: ['quotes'],
          date_from: '2026-04-20',
          date_till: '2026-09-03',
          clamped: true,
          requested_sessions: 90,
        }),
      ),
    );

    renderMarketData();

    await userEvent.click(await screen.findByRole('button', { name: 'Настроить запуск' }));
    const form = await screen.findByRole('dialog', { name: 'Запустить догон' });
    await userEvent.click(within(form).getByLabelText('Всё доступное окно'));

    const from = within(form).getByLabelText('Начало');
    await userEvent.clear(from);
    await userEvent.type(from, '01.01.2019');
    const till = within(form).getByLabelText('Конец');
    await userEvent.clear(till);
    await userEvent.type(till, '31.12.2030');

    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    const notice = await screen.findByText('Диапазон ограничен доступным окном');
    const section = notice.closest('section');
    expect(section).not.toBeNull();

    // Оба диапазона рядом: введённый — контекст показа, не серверное поле.
    expect(within(section as HTMLElement).getByText('01.01.2019 — 31.12.2030')).toBeInTheDocument();
    expect(within(section as HTMLElement).getByText('20.04.2026 — 03.09.2026')).toBeInTheDocument();
  });
});

describe('состояние после перезапуска сборщика', () => {
  it('idle не утверждает перезапуск как факт', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => HttpResponse.json(coverageFixture())),
      http.get('*/api/market-data/catchup', () => HttpResponse.json(catchupFixture('idle'))),
    );

    renderMarketData();

    expect(await screen.findByText('Не запущен')).toBeInTheDocument();
    // Интерфейс не сообщает «сервер перезапущен»: текущие поля этого не
    // доказывают (FR-035).
    expect(screen.queryByText(/перезапущен/i)).not.toBeInTheDocument();
  });
});
