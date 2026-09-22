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
 * Блок группы в сводке.
 *
 * Поиск идёт по признаку макета, а не по названию: те же названия групп есть
 * и в форме запуска (она в разметке всегда, как закрытый `dialog`), и в
 * таблице источников внутри раскрытия.
 */
async function groupBlock(group: string): Promise<HTMLElement> {
  await screen.findByRole('heading', { name: 'Группы данных' });
  const block = document.querySelector(`[data-od-id="group-${group}"]`);
  if (block === null) throw new Error(`Группа «${group}» не найдена`);
  return block as HTMLElement;
}

async function groupsSection(): Promise<HTMLElement> {
  return (await screen.findByRole('heading', { name: 'Группы данных' })).closest(
    'section',
  ) as HTMLElement;
}

describe('сводка полноты', () => {
  it('ошибка сводки не оставляет страницу в бесконечном ожидании', async () => {
    server.use(
      http.get('/api/market-data/coverage', () =>
        HttpResponse.json({ detail: 'coverage calculation failed' }, { status: 500 }),
      ),
    );
    renderMarketData();

    expect(
      await screen.findByRole('heading', { name: 'Не удалось прочитать состояние данных' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Читаем состояние данных…')).not.toBeInTheDocument();
  });

  it('показывает все пять групп с итогом и объёмом собранного', async () => {
    renderMarketData();

    expect(await screen.findByRole('heading', { name: 'Рыночные данные' })).toBeInTheDocument();

    const section = await groupsSection();
    // Названия групп берутся из заголовков строк: те же слова встречаются и в
    // таблице источников внутри раскрытия.
    const titles = [...section.querySelectorAll('.group-title')].map((node) => node.textContent);
    expect(titles).toEqual([
      'Котировки',
      'Агрегаты',
      'Глобальные ряды',
      'Позиции по фьючерсам',
      'Справочники',
    ]);

    // 255 из 314 — итог «частично», а не доля, выданная за качество (FR-010).
    const quotes = await groupBlock('quotes');
    expect(within(quotes).getByText('255 / 314')).toBeInTheDocument();
    expect(within(quotes).getByText('Частично')).toBeInTheDocument();
  });

  it('раскрытие группы называет источник поимённо', async () => {
    renderMarketData();

    // У глобальных рядов четыре источника: «глобальные ряды не собраны» без
    // имени ряда — не диагноз (FR-032).
    const global = await groupBlock('global');
    const sources = within(global).getByRole('table');
    for (const title of ['Глобальные ряды', 'Курсы и ставка ЦБ', 'Brent', 'Состав индекса']) {
      expect(within(sources).getByText(title)).toBeInTheDocument();
    }
    expect(
      within(global).getByText(/успех одного не закрывает пропуск другого/),
    ).toBeInTheDocument();
  });

  it('ошибка источника названа днём и причиной', async () => {
    // «Ошибка источника» без дня — состояние, с которым нечего делать:
    // проверить у источника нечего.
    server.use(
      http.get('*/api/market-data/coverage', () => {
        const report = coverageFixture();
        const global = report.groups.find((row) => row.group === 'global');
        const brent = global?.sources.find((source) => source.source_id === 'brent');
        if (brent !== undefined) {
          brent.status = 'failed';
          brent.failures = [
            { session_date: '2026-09-02', reason: 'источник не ответил вовремя' },
            { session_date: '2026-08-29', reason: 'HTTP 503 от источника' },
          ];
          brent.failures_total = 2;
        }
        return HttpResponse.json(report);
      }),
    );

    renderMarketData();

    const global = await groupBlock('global');
    expect(within(global).getByText(/Неудачи по дням · 2/)).toBeInTheDocument();
    expect(within(global).getByText('02.09.2026')).toBeInTheDocument();
    expect(within(global).getByText('источник не ответил вовремя')).toBeInTheDocument();
    expect(within(global).getByText('HTTP 503 от источника')).toBeInTheDocument();
  });

  it('старый справочник требует проверки без ложной ошибки и покрытия по сессиям', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => {
        const report = coverageFixture();
        const reference = report.groups.find((row) => row.group === 'reference');
        const sectors = reference?.sources.find((source) => source.source_id === 'equity_sectors');
        if (reference !== undefined && sectors !== undefined) {
          reference.requires_audit = 1;
          sectors.status = 'partial';
          sectors.requires_audit = 1;
        }
        return HttpResponse.json(report);
      }),
    );

    renderMarketData();

    const reference = await groupBlock('reference');
    expect(within(reference).getAllByText('Нужна проверка')).toHaveLength(2);
    expect(within(reference).queryByText('Ошибка источника')).not.toBeInTheDocument();
    expect(
      within(reference).getByText('Старый ответ не подтверждён новым правилом'),
    ).toBeInTheDocument();
    expect(within(reference).queryByText(/из \d+ сессий/)).not.toBeInTheDocument();
  });

  it('сохранённая история видна даже без подтверждения полноты', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => {
        const report = coverageFixture();
        const quotes = report.groups.find((row) => row.group === 'quotes')!;
        quotes.requires_audit = 313;
        quotes.sessions_covered = 1;
        quotes.rows_with_values = 91465;
        return HttpResponse.json(report);
      }),
    );
    renderMarketData();
    const quotes = await groupBlock('quotes');
    expect(within(quotes).getByText('История не проверена')).toBeInTheDocument();
    expect(within(quotes).getByText(/91\s465 строк со значениями/)).toBeInTheDocument();
    expect(within(quotes).getByText(/Сохранённые данные на месте/)).toBeInTheDocument();
    expect(within(quotes).getByText('1 / 314')).toBeInTheDocument();
  });

  it('реальный отказ справочника показан отдельно от отсутствия проверки', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => {
        const report = coverageFixture();
        const reference = report.groups.find((row) => row.group === 'reference');
        const lots = reference?.sources.find((source) => source.source_id === 'equity_lot_sizes');
        if (lots !== undefined) {
          lots.status = 'failed';
          lots.last_checked_at = '2026-09-03T18:00:00Z';
          lots.reason = 'ISS вернул некорректный справочник';
        }
        return HttpResponse.json(report);
      }),
    );

    renderMarketData();

    const reference = await groupBlock('reference');
    expect(within(reference).getByText('Ошибка источника')).toBeInTheDocument();
    expect(within(reference).getByText('Ошибка')).toBeInTheDocument();
    expect(within(reference).getByText(/ISS вернул некорректный справочник/)).toBeInTheDocument();
  });

  it('неполнота позиций объяснена числами, а не догадкой', async () => {
    renderMarketData();

    // Фьючерс есть не у каждой бумаги, и его отсутствие — не пропуск
    // (FR-010, FR-013).
    const positions = await groupBlock('positions');
    expect(within(positions).getByText(/фьючерс есть у 63 из 243 бумаг/)).toBeInTheDocument();
  });

  it('состав, которого нет, назван неизвестным, а не нулём', async () => {
    // «0 из 0» читалось бы как «фьючерса нет ни у одной бумаги», хотя состав
    // просто не посчитан: без собранной сессии его взять неоткуда (FR-019a).
    server.use(
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          coverageFixture({ universe: { assets: 0, assets_with_futures: 0, asof_date: null } }),
        ),
      ),
    );

    renderMarketData();

    const positions = await groupBlock('positions');
    expect(within(positions).getByText(/состав бумаг не посчитан/)).toBeInTheDocument();
    expect(within(positions).queryByText(/0 из 0/)).not.toBeInTheDocument();
  });

  it('состав старше даты сводки — и это сказано', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          coverageFixture({
            universe: { assets: 243, assets_with_futures: 63, asof_date: '2026-09-01' },
          }),
        ),
      ),
    );

    renderMarketData();

    const section = await groupsSection();
    // Дата сама по себе ничего не объясняет — сказано, откуда она взялась.
    expect(within(section).getByText(/по последней собранной сессии/)).toBeInTheDocument();
    expect(within(section).getByText('01.09.2026')).toBeInTheDocument();
  });

  it('следующий сбор: момент и взятая сессия — разные строки', async () => {
    // При отставании сессия, которую возьмёт сбор, лежит в прошлом. Рядом со
    // словом «следующий» она читалась бы как ошибка.
    //
    // И порога такой сбор не ждёт: он возьмёт недостающую сессию следующим же
    // прогоном. «Сегодня после 23:30» рядом с апрельской датой обещало, что
    // её соберут вечером (FR-054).
    server.use(
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          coverageFixture({ next_session: '2026-04-24', next_session_closed: true }),
        ),
      ),
    );

    renderMarketData();

    const schedule = (await screen.findByText('Следующий сбор')).closest('ul') as HTMLElement;
    expect(schedule.textContent).toMatch(/24\.04\.2026 — ближайшим прогоном/);
    expect(schedule.textContent).not.toMatch(/сегодня после/);
    expect(within(schedule).getByText('Последняя закрытая')).toBeInTheDocument();
  });

  it('в неторговый день сбор не обещается на сегодня', async () => {
    // Порог имеет смысл только в торговый день: в воскресенье «сегодня после
    // 23:30» обещает сбор сессии, которой не будет.
    server.use(
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          coverageFixture({ next_session: '2026-09-03', next_session_closed: false }),
        ),
      ),
    );

    renderMarketData();

    const schedule = (await screen.findByText('Следующий сбор')).closest('ul') as HTMLElement;
    expect(schedule.textContent).toMatch(/03\.09\.2026 после \d\d:\d\d/);
  });

  it('клетка календаря открывает сведения о дате', async () => {
    server.use(
      http.get('*/api/market-data/calendar', () =>
        HttpResponse.json({
          month: '2026-09',
          today: '2026-09-03',
          days: [
            {
              date: '2026-09-02',
              kind: 'session',
              groups: { quotes: 'collected', positions: 'missing' },
            },
          ],
        }),
      ),
    );

    renderMarketData();

    await userEvent.click(await screen.findByRole('button', { name: 'Сессия 02.09.2026' }));

    const dialog = await screen.findByRole('dialog', { name: 'Сессия 02.09.2026' });
    expect(within(dialog).getByText('Позиции по фьючерсам')).toBeInTheDocument();
    expect(within(dialog).getByText('Не собрано')).toBeInTheDocument();
    // Собрать одну сессию — тот же ручной сбор диапазоном в один день.
    expect(within(dialog).getByRole('button', { name: 'Собрать эту сессию' })).toBeInTheDocument();
  });

  it('не выводит конкретных значений наблюдений', async () => {
    const { container } = renderMarketData();
    await groupsSection();

    // Отчёт о полноте, а не просмотр данных (FR-008).
    expect(container.textContent).not.toMatch(/314[,.]22|125484/);
  });

  it('группа без истории не выглядит недобранной', async () => {
    renderMarketData();

    const reference = await groupBlock('reference');
    expect(within(reference).getAllByText('текущее состояние').length).toBeGreaterThan(0);
    expect(within(reference).getByText(/истории у него нет/)).toBeInTheDocument();
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

  it('расхождение «покрыто, но пусто» видно и в уведомлении, и в строке группы', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => HttpResponse.json(anomalyCoverageFixture())),
    );

    renderMarketData();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Сессии покрыты. Значения отсутствуют.');
    expect(alert).toHaveTextContent(
      /Догон пропущенных сессий сам по себе это расхождение не исправит/,
    );

    // Состояние передано текстом, а не только цветом (FR-052).
    const positions = await groupBlock('positions');
    expect(within(positions).getByText('Значения отсутствуют')).toBeInTheDocument();
  });

  it('без расхождения уведомления нет', async () => {
    server.use(http.get('*/api/market-data/coverage', () => HttpResponse.json(coverageFixture())));

    renderMarketData();
    await groupsSection();

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
    await groupsSection();
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
