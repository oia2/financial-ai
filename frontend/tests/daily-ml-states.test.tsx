/**
 * Восемь состояний раздела «Ранжирование» (US4, FR-046–FR-055).
 *
 * Проверяется не вёрстка, а утверждения: две даты не сливаются в одну, время
 * следующей проверки не выдаётся за время начала расчёта, пауза не выдаётся за
 * остановку сбора данных, а «проверить сейчас» не читается как пересчёт.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { DailyMlStatusDto } from '@/entities/daily-ml';

import { historyFixture, runFixture, statusFixture } from './msw/daily-ml';
import { http, HttpResponse, server } from './msw/server';

function renderWith(status: Partial<DailyMlStatusDto>) {
  server.use(http.get('*/api/daily-ml/status', () => HttpResponse.json(statusFixture(status))));

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

describe('состояния раздела «Ранжирование»', () => {
  it('всё актуально: две даты показаны как отдельные величины', async () => {
    renderWith({ status: 'up_to_date' });

    expect(await screen.findByText('Ранжирование выполнено')).toBeInTheDocument();

    // FR-047: готовность данных и готовность модели — разные величины, и у
    // каждой своя подпись. Одна дата на двоих скрыла бы отставание.
    const data = screen.getByText('Данные готовы по').closest('div')!;
    const model = screen.getByText('Ранжирование выполнено по').closest('div')!;
    expect(within(data).getByText('11.09.2026')).toBeInTheDocument();
    expect(within(model).getByText('11.09.2026')).toBeInTheDocument();
  });

  it('ожидание данных: время готовности не обещается', async () => {
    renderWith({ status: 'waiting', latest_data_ready: null, latest_ml_success: '2026-09-10' });

    expect(await screen.findByText('Ожидание данных')).toBeInTheDocument();
    expect(screen.getByText(/Время готовности заранее неизвестно/)).toBeInTheDocument();
    expect(screen.getByText('Готовый вход пока отсутствует')).toBeInTheDocument();
  });

  it('ожидание данных: сказано, каких данных не хватает', async () => {
    // «Ожидаются данные» без ответа «каких?» оставляет человека гадать, а
    // расчёт готовности этот ответ уже содержит (US1/AC5).
    renderWith({
      status: 'waiting',
      latest_data_ready: null,
      blocking_groups: [
        { group: 'aggregates', title: 'агрегаты' },
        { group: 'positions', title: 'позиции по фьючерсам' },
      ],
    });

    expect(
      await screen.findByText(/Не хватает: агрегаты, позиции по фьючерсам\./),
    ).toBeInTheDocument();
  });

  it('идёт прогон: показаны дата и начало, но не доля выполненного', async () => {
    renderWith({
      status: 'running',
      current: { asof_date: '2026-09-11', started_at: '2026-09-11T17:30:04+00:00', attempt: 1 },
    });

    // Заголовок состояния, а не сводка блока «Работа системы»: текст один,
    // но роли разные.
    expect(await screen.findByRole('heading', { name: 'Идёт ранжирование' })).toBeInTheDocument();
    expect(screen.getByText('Ранжирование за')).toBeInTheDocument();

    // FR-072: доли готовности внутри одной даты нет. Полоса по таймеру
    // браузера была бы выдумкой — сервер процента не сообщает.
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it('отставание: очередь показана датами и счётным прогрессом', async () => {
    renderWith({
      status: 'lagging',
      latest_ml_success: '2026-09-09',
      queue: [
        { asof_date: '2026-09-10', status: 'queued' },
        { asof_date: '2026-09-11', status: 'queued' },
      ],
      queue_progress: { completed: 1, total: 3 },
    });

    expect(await screen.findByText('Ранжирование отстаёт')).toBeInTheDocument();

    const progress = screen.getByText(/Обработано/);
    expect(progress).toHaveTextContent('Обработано 1 из 3');
    expect(screen.getByText('10.09.2026')).toBeInTheDocument();
    expect(screen.getByText('Ожидают обработки · выполняются по одной')).toBeInTheDocument();
  });

  it('отказ: причина названа, предыдущие результаты не потеряны, повтор доступен', async () => {
    server.use(
      http.get('*/api/daily-ml/runs', ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        if (status === 'failed') {
          return HttpResponse.json(
            historyFixture([runFixture({ id: 144, status: 'failed', duration_seconds: null })]),
          );
        }
        return HttpResponse.json(historyFixture([runFixture()]));
      }),
    );

    renderWith({ status: 'failed', last_error: 'звено ранжирования недоступно' });

    expect(await screen.findByText('Последний прогон не удался')).toBeInTheDocument();
    expect(screen.getByText('звено ранжирования недоступно')).toBeInTheDocument();
    expect(screen.getByText(/Предыдущие результаты\s+сохранены/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  it('пауза: сказано, что сбор рыночных данных продолжается', async () => {
    renderWith({ status: 'paused', paused: true });

    expect(await screen.findByText('Автоматическое ранжирование остановлено')).toBeInTheDocument();
    // FR-055: пауза останавливает ранжирование, а не сбор. Слитое прочтение
    // этих двух механизмов — самая дорогая ошибка чтения экрана.
    expect(screen.getByText('Сбор рыночных данных продолжается.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить автоматическое/ })).toBeInTheDocument();
  });

  it('разрыв данных: расчёт не обещан, предложен переход в рыночные данные', async () => {
    renderWith({ status: 'data_gap', data_gap_sessions: 7 });

    expect(await screen.findByText('Разрыв данных')).toBeInTheDocument();
    expect(screen.getByText(/Данные отстают на 7 торговых сессий/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Открыть рыночные данные/ })).toBeInTheDocument();
  });

  it('молчание сборщика не выдаётся за отсутствие готовых данных', async () => {
    // «Готовой даты нет» и «о готовности сказать нечего» — разные утверждения.
    renderWith({ status: 'waiting', latest_data_ready: null, readiness_known: false });

    expect(
      await screen.findByText('Сборщик не ответил: готовность неизвестна'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Готовый вход пока отсутствует')).not.toBeInTheDocument();
  });

  it('устаревший вход: пересчёт обещан только для последней актуальной даты', async () => {
    renderWith({ status: 'up_to_date', stale_latest: true });

    expect(await screen.findByText('Входные данные изменились')).toBeInTheDocument();
    expect(
      screen.getByText(/Исторические даты автоматически не\s+пересчитываются/),
    ).toBeInTheDocument();
    expect(screen.getByText('Результат по прежнему входу')).toBeInTheDocument();
  });
});

describe('следующий запуск', () => {
  it('показывает время проверки и не показывает времени начала расчёта', async () => {
    renderWith({ status: 'waiting' });

    await screen.findByText('Ожидание данных');
    await userEvent.click(screen.getByText('Работа системы'));

    // FR-053, FR-054: срок сбора данных — известный момент, начало расчёта — нет.
    const next = screen.getByText(/Данные торговой сессии собираются после/);
    // 16:30 UTC, а не 19:30: пояс прогона закреплён на UTC, и московские часы
    // здесь означали бы, что перевод в пояс зрителя не выполняется (FR-073).
    expect(next).toHaveTextContent('16:30');
    expect(next).toHaveTextContent('считать начнёт по готовности данных');
    // Срок сбора не выдаётся за периодичность проверки: ранжирование ищет
    // работу непрерывно, и прежний текст утверждал обратное.
    expect(next).not.toHaveTextContent('Следующий цикл системы');
    // Даты у срока нет: календарь эмпирический и следующей сессии не знает, а
    // дата, посчитанная часами, в выходной обещала бы вечер без торгов (FR-054a).
    expect(next.textContent).not.toMatch(/\d{2}\.\d{2}\.\d{4}/);
    expect(next).toHaveTextContent('Если сессии не было, собирать нечего');
  });

  it('после остановки запуск не планируется', async () => {
    renderWith({ status: 'paused', paused: true });

    await screen.findByText('Автоматическое ранжирование остановлено');
    await userEvent.click(screen.getByText('Работа системы'));

    expect(screen.getByText('Не запланирован — ранжирование остановлено.')).toBeInTheDocument();
  });
});

describe('управление', () => {
  it('«проверить новые данные» не читается как принудительный пересчёт', async () => {
    let called = false;
    server.use(
      http.post('*/api/daily-ml/reconcile', () => {
        called = true;
        return HttpResponse.json({ queued: 0, already_up_to_date: true });
      }),
    );

    renderWith({ status: 'up_to_date' });

    await screen.findByText('Ранжирование выполнено');
    await userEvent.click(screen.getByRole('button', { name: 'Проверить сейчас' }));

    expect(called).toBe(true);
    expect(
      await screen.findByText('Новых готовых данных нет. Уже посчитанное не пересчитывается.'),
    ).toBeInTheDocument();
  });

  it('недоступность сборщика не выдаётся за отказ ранжирования', async () => {
    server.use(
      http.post('*/api/daily-ml/reconcile', () =>
        HttpResponse.json(
          { detail: { code: 'worker_unavailable', message: 'сборщик данных недоступен' } },
          { status: 503 },
        ),
      ),
    );

    renderWith({ status: 'up_to_date' });

    await screen.findByText('Ранжирование выполнено');
    await userEvent.click(screen.getByRole('button', { name: 'Проверить сейчас' }));

    expect(
      await screen.findByText('Сборщик данных недоступен. Команда не выполнена.'),
    ).toBeInTheDocument();
  });
});

describe('общий баннер процессов', () => {
  it('показывает догон и ранжирование независимо друг от друга', async () => {
    server.use(
      http.get('*/api/market-data/catchup', () =>
        HttpResponse.json({
          status: 'running',
          requested: 90,
          closed: 18,
          remaining: 71,
          failed: 1,
          current_session: '2026-05-14',
          started_at: '2026-09-12T09:00:00+00:00',
          finished_at: null,
          message: null,
          groups: ['quotes'],
          date_from: '2026-04-20',
          date_till: '2026-09-03',
        }),
      ),
    );

    renderWith({
      status: 'running',
      current: { asof_date: '2026-09-11', started_at: '2026-09-11T17:30:04+00:00', attempt: 1 },
    });

    // Ни один процесс не вытесняет другой: оба идут и оба видны.
    expect(await screen.findByText('Догон данных продолжается')).toBeInTheDocument();
    expect(screen.getByText('Выполняется ранжирование')).toBeInTheDocument();
  });
});
