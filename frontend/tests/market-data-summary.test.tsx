/**
 * Сводка полноты рыночных данных (US1, FR-008–FR-018).
 *
 * Проверяется то, ради чего раздел заводился: покрытие и наличие значений —
 * две разные проверки, группа без истории не выглядит недобранной, а
 * непереданное число показывается прочерком, а не нулём.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { navigate } from '@/app/router';
import { AppProviders } from '@/app/providers';

import { anomalyCoverageFixture, coverageFixture } from './msw/market-data';
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

/**
 * Строка группы в сводке.
 *
 * Поиск ограничен таблицей: те же названия групп есть в форме запуска, и она
 * присутствует в разметке всегда — как закрытый `dialog`.
 */
async function rowOf(title: string) {
  const table = await screen.findByRole('table');
  const cell = within(table).getByText(title);
  const row = cell.closest('tr');
  if (row === null) throw new Error(`Строка группы «${title}» не найдена`);
  return row;
}

async function summaryTable() {
  return screen.findByRole('table');
}

describe('сводка полноты', () => {
  it('показывает все пять групп с покрытием и долей значений', async () => {
    renderMarketData();

    expect(await screen.findByRole('heading', { name: 'Рыночные данные' })).toBeInTheDocument();

    const table = await summaryTable();
    for (const title of [
      'Котировки',
      'Агрегаты',
      'Глобальные ряды',
      'Позиции по фьючерсам',
      'Справочники',
    ]) {
      expect(within(table).getByText(title)).toBeInTheDocument();
    }

    // 255 из 314 — 81,2% покрытия при 96,1% строк со значениями: два числа,
    // а не одно (FR-009).
    const quotes = await rowOf('Котировки');
    expect(within(quotes).getByText('81,2%')).toBeInTheDocument();
    expect(within(quotes).getByText('96,1%')).toBeInTheDocument();
    expect(within(quotes).getByText('255 / 314')).toBeInTheDocument();
    // Не покрыто — разность окна и покрытых сессий.
    expect(within(quotes).getByText('59')).toBeInTheDocument();
  });

  it('не выводит конкретных значений наблюдений', async () => {
    const { container } = renderMarketData();
    await summaryTable();

    // Отчёт о полноте, а не просмотр данных (FR-008).
    expect(container.textContent).not.toMatch(/314[,.]22|125484/);
  });

  it('группа без истории не выглядит недобранной', async () => {
    renderMarketData();

    const reference = await rowOf('Справочники');
    expect(within(reference).getByText('История не ведётся')).toBeInTheDocument();
    expect(within(reference).getByText('Текущее состояние')).toBeInTheDocument();
    // Ноль вместо отсутствующего покрытия читался бы как «ничего не собрано».
    expect(within(reference).queryByText('0,0%')).not.toBeInTheDocument();
  });

  it('показывает прочерк вместо непереданного числа строк', async () => {
    renderMarketData();

    await userEvent.click(await screen.findByRole('button', { name: 'Сведения: Котировки' }));

    const dialog = await screen.findByRole('dialog');
    const total = within(dialog).getByText('Всего строк').closest('div');
    expect(total).not.toBeNull();
    // Прочерк — «значения нет», а не ноль (FR-015).
    expect(within(total as HTMLElement).getByText('—')).toBeInTheDocument();
  });

  it('панель сведений закрывается по Escape и возвращает фокус', async () => {
    renderMarketData();

    const trigger = await screen.findByRole('button', { name: 'Сведения: Котировки' });
    await userEvent.click(trigger);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('расхождение «покрыто, но пусто» видно и в уведомлении, и в строке', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => HttpResponse.json(anomalyCoverageFixture())),
    );

    renderMarketData();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Сессии покрыты. Значения отсутствуют.');
    expect(alert).toHaveTextContent(
      /Догон пропущенных сессий сам по себе это расхождение не исправит/,
    );

    const positions = await rowOf('Позиции по фьючерсам');
    expect(positions).toHaveClass('anomaly-row');
    // Состояние передано текстом, а не только цветом (FR-052).
    expect(within(positions).getByText('Значения отсутствуют')).toBeInTheDocument();
  });

  it('без расхождения уведомления нет', async () => {
    server.use(http.get('*/api/market-data/coverage', () => HttpResponse.json(coverageFixture())));

    renderMarketData();
    await summaryTable();

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('перечитывает сводку по действию в шапке', async () => {
    let calls = 0;
    server.use(
      http.get('*/api/market-data/coverage', () => {
        calls += 1;
        return HttpResponse.json(coverageFixture());
      }),
    );

    renderMarketData();
    await summaryTable();
    expect(calls).toBe(1);

    await userEvent.click(screen.getByRole('button', { name: 'Обновить сводку рыночных данных' }));

    await waitFor(() => expect(calls).toBeGreaterThan(1));
  });

  it('пустое хранилище объясняется, а не выглядит аварией', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          { detail: { code: 'calendar_empty', message: 'календарь пуст' } },
          { status: 422 },
        ),
      ),
    );

    renderMarketData();

    const panel = (await screen.findByText('Хранилище пока пусто')).closest('div');
    expect(panel).not.toBeNull();
    expect(within(panel as HTMLElement).getByText(/первичная загрузка/)).toBeInTheDocument();
  });
});

describe('остановка сбора', () => {
  it('переключает сбор и говорит, что ранжирования это не касается', async () => {
    let paused = false;
    server.use(
      http.get('*/api/market-data/settings', () => HttpResponse.json({ paused })),
      http.put('*/api/market-data/settings', async ({ request }) => {
        const body = (await request.json()) as { paused: boolean };
        paused = body.paused;
        return HttpResponse.json({ paused });
      }),
    );

    renderMarketData();

    // FR-029e: подпись называет АВТОСБОР, а не «сбор». «Остановить сбор»
    // читалось как остановка уже идущего прогона, чем кнопка не является: она
    // выключает автоматический режим, а начатую сессию доводит до конца.
    const button = await screen.findByRole('button', {
      name: 'Поставить автоматический сбор данных на паузу',
    });
    expect(button).toHaveTextContent('Пауза автосбора');
    expect(button).toHaveAttribute('aria-pressed', 'false');

    await userEvent.click(button);

    const resumed = await screen.findByRole('button', {
      name: 'Возобновить автоматический сбор данных',
    });
    expect(resumed).toHaveTextContent('Возобновить автосбор');
    expect(resumed).toHaveAttribute('aria-pressed', 'true');
  });

  it('после перезапуска сборщика показывает, что сбор снова идёт', async () => {
    // Состояние живёт в процессе сборщика: перезапуск возвращает сбор, и экран
    // обязан читать это с сервера, а не помнить прошлое нажатие (FR-029f).
    server.use(http.get('*/api/market-data/settings', () => HttpResponse.json({ paused: false })));

    renderMarketData();

    expect(
      await screen.findByRole('button', { name: 'Поставить автоматический сбор данных на паузу' }),
    ).toBeInTheDocument();
  });
});
