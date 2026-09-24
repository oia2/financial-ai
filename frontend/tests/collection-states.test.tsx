/**
 * Состояния раздела «Рыночные данные» — contracts/ui-states.md (spec 008).
 *
 * Заменяет прежние проверки состояний догона: раздел переехал на макет
 * `market-data.html`, и язык у него другой. Проверяется не вёрстка, а то, ради
 * чего эти состояния заведены:
 *
 *  - виден идущий источник и следующий за ним (FR-003);
 *  - у каждого пропуска названа причина (FR-002);
 *  - панель не исчезает, когда прогон кончился, и показывает итог (FR-025);
 *  - остановка — состояние, а не уведомление;
 *  - пауза автосбора не обрывает начатый прогон, но видна.
 */

import { QueryClient } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { CatchupStateDto, LinkEventDto, RunsDto, RunSummaryDto } from '@/entities/market-data';
import { catchupQueryKey } from '@/entities/market-data';

import { catchupFixture, coverageFixture } from './msw/market-data';
import { http, HttpResponse, server } from './msw/server';

function renderWith(
  state: CatchupStateDto,
  runs: RunSummaryDto[] = [],
  events: LinkEventDto[] = [],
  skips: RunsDto['skips'] = [],
) {
  server.use(
    http.get('*/api/market-data/catchup', () => HttpResponse.json(state)),
    http.get('*/api/market-data/runs', () =>
      HttpResponse.json({
        runs,
        events,
        events_total: events.length,
        skips,
        skips_total: skips.length,
      }),
    ),
  );

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

const FINISHED_RUN: RunSummaryDto = {
  run_id: 'run-1',
  mode: 'daily',
  started_at: '2026-09-17T14:58:00Z',
  finished_at: '2026-09-17T15:14:00Z',
  status: 'failed',
  sessions: { requested: 12, collected: 9, partial: 1, failed: 1, skipped: 1, pending: 0 },
  history_limited: false,
  failures: [
    {
      source_id: 'brent',
      title: 'Brent',
      session_date: '2026-09-09',
      reason: 'источник не ответил вовремя',
    },
  ],
};

describe('ход прогона', () => {
  it('идёт: назван идущий источник и следующий за ним', async () => {
    renderWith(catchupFixture('running'));

    // Идущий источник — тот, ради которого лента и заведена: без него долгий
    // шаг неотличим от зависания.
    // Поиск ограничен лентой: те же названия источников есть в раскрытии
    // групп сводки.
    const rail = (await screen.findByText('идёт')).closest('.source-rail') as HTMLElement;
    expect(within(rail).getByText('Агрегаты торгов')).toBeInTheDocument();
    expect(within(rail).getByText('следующий')).toBeInTheDocument();
  });

  it('идёт: суточный источник не бывает «следующим»', async () => {
    // Торговый календарь синхронизируется ОДИН раз перед циклом сессий. Пока
    // «следующим» считался первый ожидающий любого вида, он оставался им до
    // конца прогона, сколько бы сессий тот ни шёл (FR-056).
    const state = catchupFixture('running');
    const current = state.current as NonNullable<CatchupStateDto['current']>;
    renderWith({
      ...state,
      current: {
        ...current,
        sources: current.sources.map((source) =>
          source.source_id === 'trading_calendar' ? { ...source, state: 'pending' } : source,
        ),
      },
    });

    const rail = (await screen.findByText('идёт')).closest('.source-rail') as HTMLElement;
    const next = within(rail).getByText('следующий').closest('.rail-item') as HTMLElement;

    expect(next.textContent).toContain('Позиции по фьючерсам');
    expect(next.textContent).not.toContain('Торговый календарь');
  });

  it('идёт: счётчик считает ровно строки ленты', async () => {
    renderWith(catchupFixture('running'));

    // Пометки области нет ни над лентой, ни у строки: владелец счёл фразу
    // «— раз в сутки, — на весь период» лишней (2026-09-23).
    const head = await waitFor(() => {
      const found = document.querySelector('.rail-head');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(head.textContent).not.toContain('раз в сутки');
    // Счётчик считает ровно то, что показано: четыре строки, из них выполнены
    // календарь и котировки (владелец, 2026-09-23).
    const shown = document.querySelectorAll('.rail .rail-item').length;
    expect(head.querySelector('b')?.textContent).toContain(`2 из ${shown}`);

    const flags = [...document.querySelectorAll('.rail .rail-flag')].map((n) => n.textContent);
    expect(flags).not.toContain('раз в сутки');
    expect(flags).not.toContain('на весь период');
  });

  it('у каждого пропуска названа причина', async () => {
    renderWith(
      catchupFixture('running', {
        skips: [
          {
            session_date: '2026-09-15',
            reason: 'attempts_exhausted',
            detail: '5 попыток из 5',
          },
        ],
        sessions: {
          requested: 3,
          collected: 1,
          partial: 0,
          failed: 0,
          skipped: 1,
          pending: 1,
          outcomes: [
            { session_date: '2026-09-14', outcome: 'collected' },
            { session_date: '2026-09-15', outcome: 'skipped' },
          ],
        },
      }),
    );

    expect(await screen.findByText('Почему пропущена 1 сессия')).toBeInTheDocument();
    expect(screen.getByText(/исчерпан предел попыток: 5 попыток из 5/)).toBeInTheDocument();
  });

  it('останавливается: состояние, а не только уведомление', async () => {
    renderWith(catchupFixture('stopping', { stop_requested: true }));

    expect(await screen.findByText('Останавливается')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Остановка запрошена' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Остановить прогон' })).not.toBeInTheDocument();
  });
});

describe('прогон закончился', () => {
  it('итог остаётся на месте панели, а не исчезает вместе с процессом', async () => {
    renderWith(catchupFixture('finished'), [FINISHED_RUN]);

    expect(await screen.findByRole('heading', { name: /Прогон закончен/ })).toBeInTheDocument();
    expect(screen.getByText(/18 собрано/)).toBeInTheDocument();
    expect(screen.getByText(/Последние прогоны/)).toBeInTheDocument();
  });

  it('незакрытое можно повторить, и это новый запуск', async () => {
    renderWith(catchupFixture('finished'), [FINISHED_RUN]);

    expect(
      await screen.findByRole('button', { name: 'Повторить несобранное' }),
    ).toBeInTheDocument();
  });

  it('прерван ошибкой: причина берётся из ответа сервера', async () => {
    renderWith(catchupFixture('failed', { reason: 'не удалось записать собранное в хранилище' }), [
      FINISHED_RUN,
    ]);

    expect(await screen.findByText('Причина остановки')).toBeInTheDocument();
    expect(screen.getByText(/не удалось записать собранное в хранилище/)).toBeInTheDocument();
  });

  it('прогонов ещё не было: единственный пустой экран', async () => {
    renderWith(catchupFixture('idle'), []);

    expect(await screen.findByText('Прогонов ещё не было')).toBeInTheDocument();
  });

  it('разрыв больше предела: отказ автосбора, а не ошибка прогона', async () => {
    renderWith(
      catchupFixture('finished', {
        skips: [
          {
            session_date: '2026-09-15',
            reason: 'gap_over_limit',
            detail: 'разрыв 41 сессии при пределе 30',
          },
        ],
      }),
      [FINISHED_RUN],
    );

    // Числа приходят с сервера: интерфейс их не выводит и не округляет. То же
    // число стоит и в списке пропусков — ищем именно уведомление.
    const notice = (await screen.findByText('Автосбор не берёт этот разрыв')).closest(
      '.run-notice',
    ) as HTMLElement;
    expect(notice).toHaveClass('error');
    expect(within(notice).getByText(/разрыв 41 сессии при пределе 30/)).toBeInTheDocument();
    expect(within(notice).getByText(/закрывается ручным сбором/)).toBeInTheDocument();
  });

  it('у остановленного прогона непройденное названо несобранным', async () => {
    // «Все сессии прогона собраны» про прогон, который бросили на середине,
    // объявляло бы собранным то, к чему даже не приступали.
    const stopped = catchupFixture('stopped');
    renderWith(
      {
        ...stopped,
        sessions: { ...stopped.sessions, requested: 90, collected: 18, failed: 0, pending: 72 },
      },
      [FINISHED_RUN],
    );

    // Три случая различаются: не собралось, пропущено с причиной и вовсе не
    // начиналось. Последнее — остаток остановленного прогона.
    expect(await screen.findByText(/не начинались: 72/)).toBeInTheDocument();
    expect(screen.queryByText('все сессии прогона собраны')).not.toBeInTheDocument();
  });

  it('остановленный прогон предлагает продолжить или отменить', async () => {
    // Остановка — не отмена: непройденные сессии никуда не делись, и человек
    // выбирает, доводить их или бросить. Молчаливый переход к «начать заново»
    // этот выбор стирал бы.
    renderWith(catchupFixture('stopped'), [FINISHED_RUN]);

    expect(await screen.findByRole('button', { name: 'Продолжить прогон' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отменить прогон' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Отменить прогон' }));

    // Отказ — состояние экрана: выбор больше не предлагается, а панель
    // показывает обычный итог прошедшего прогона.
    expect(screen.queryByRole('button', { name: 'Продолжить прогон' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ручной сбор' })).toBeInTheDocument();
  });

  it('журнал событий: что было последние минуты', async () => {
    // Лента источников показывает НЫНЕШНЕЕ положение дел и произошедшее
    // стирает: собравшийся источник в ней просто становится галочкой.
    const running = catchupFixture('running');
    renderWith({
      ...running,
      log: [
        { at: '2026-09-10T09:39:52Z', text: 'Состав индекса · собран' },
        { at: '2026-09-10T09:31:10Z', text: 'Сессия 15.09 пропущена: исчерпан предел попыток' },
      ],
    });

    expect(await screen.findByText(/Журнал событий/)).toBeInTheDocument();
    expect(screen.getByText('Состав индекса · собран')).toBeInTheDocument();
    expect(screen.getByText('Сессия 15.09 пропущена: исчерпан предел попыток')).toBeInTheDocument();
  });

  it('обычный вечерний сбор: шкала считает источники, а не сессии', async () => {
    // Одна сессия — и шкала по сессиям выродилась бы в один сегмент на всю
    // ширину, который не сообщает ничего. Таких прогонов большинство.
    const one = catchupFixture('running');
    renderWith({
      ...one,
      sessions: { ...one.sessions, requested: 1, pending: 0, collected: 1 },
    });

    expect(await screen.findByText(/· источники/)).toBeInTheDocument();
    // В счёт входят только посессионные источники: суточный календарь не в нём.
    expect(screen.getByRole('img', { name: /собрано 1 источников из 3/i })).toBeInTheDocument();
  });

  it('длинный прогон: дорожка сплошная, а не полоса из одних зазоров', async () => {
    // При 216 сессиях 215 зазоров по 3px дают 645 пикселей там, где дорожке
    // отведено около 400: сегменты схлопываются в ноль, и шкалы не видно.
    const long = catchupFixture('running');
    renderWith({ ...long, sessions: { ...long.sessions, requested: 216, pending: 197 } });

    const track = await screen.findByRole('img', { name: /Собрано/ });
    expect(track).toHaveClass('dense');
  });

  it('короткий прогон: сегменты разделены зазорами', async () => {
    const short = catchupFixture('running');
    renderWith({ ...short, sessions: { ...short.sessions, requested: 12, pending: 9 } });

    const track = await screen.findByRole('img', { name: /Собрано/ });
    expect(track).not.toHaveClass('dense');
  });

  it('идущий прогон называет, сколько идёт и когда был последний ответ', async () => {
    // В макете у идущего прогона две строки итога: «Последний ответ источника —
    // N с назад» и «Идёт — MM:SS». Это факты, а не обещание длительности:
    // сколько прогон ещё продлится, раздел не говорит (FR-027).
    renderWith(catchupFixture('running'));

    expect(await screen.findByText('Последний ответ источника')).toBeInTheDocument();
    expect(screen.getByText(/назад/)).toBeInTheDocument();
    expect(screen.getByText('Идёт')).toBeInTheDocument();
  });

  it('законченный прогон называет длительность', async () => {
    renderWith(catchupFixture('finished'), [FINISHED_RUN]);

    expect(await screen.findByText('Длительность')).toBeInTheDocument();
  });

  it('журнал не прячет частичную сессию внутри собранных', async () => {
    renderWith(catchupFixture('finished'), [FINISHED_RUN]);

    const journal = await screen.findByText(/Последние прогоны/);
    const details = journal.closest('details') as HTMLElement;
    expect(within(details).getByText(/1 частично/)).toBeInTheDocument();
    expect(within(details).getByText(/9 собрано/)).toBeInTheDocument();
  });

  it('после завершения перечитывает сводку, журнал и календарь', async () => {
    let coverageReads = 0;
    let runsReads = 0;
    let calendarReads = 0;
    const running = catchupFixture('running');

    server.use(
      http.get('*/api/market-data/catchup', () => HttpResponse.json(running)),
      http.get('*/api/market-data/coverage', () => {
        coverageReads += 1;
        return HttpResponse.json(coverageFixture());
      }),
      http.get('*/api/market-data/runs', () => {
        runsReads += 1;
        return HttpResponse.json({
          runs: [FINISHED_RUN],
          events: [],
          events_total: 0,
          skips: [],
          skips_total: 0,
        });
      }),
      http.get('*/api/market-data/calendar', () => {
        calendarReads += 1;
        return HttpResponse.json({
          earliest_month: '2026-09',
          month: '2026-09',
          today: '2026-09-17',
          days: [],
        });
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchInterval: false } },
    });
    window.history.pushState(null, '', '/market-data');
    navigate('market-data');
    render(
      <AppProviders client={client}>
        <AppShell />
      </AppProviders>,
    );

    await waitFor(() => {
      expect(coverageReads).toBeGreaterThan(0);
      expect(runsReads).toBeGreaterThan(0);
      expect(calendarReads).toBeGreaterThan(0);
    });
    const before = { coverageReads, runsReads, calendarReads };

    act(() => {
      client.setQueryData(catchupQueryKey, catchupFixture('finished'));
    });

    await waitFor(() => {
      expect(coverageReads).toBeGreaterThan(before.coverageReads);
      expect(runsReads).toBeGreaterThan(before.runsReads);
      expect(calendarReads).toBeGreaterThan(before.calendarReads);
    });
  });

  it('связи со сборщиком нет: панель не выглядит идущей', async () => {
    // Иначе на экране спорят два блока: уведомление говорит «сборщик
    // недоступен», а панель рядом отсчитывает сессии, будто сбор продолжается
    // (contracts/ui-states.md).
    server.use(
      http.get('*/api/market-data/catchup', () => HttpResponse.json(catchupFixture('running'))),
    );
    renderWith(catchupFixture('running'));

    // Состояние прочитано, потом сборщик пропал.
    expect(await screen.findByRole('heading', { name: /Собираем сессию/ })).toBeInTheDocument();

    server.use(
      http.get('*/api/market-data/catchup', () =>
        HttpResponse.json(
          { detail: { code: 'worker_unavailable', message: 'сборщик недоступен' } },
          { status: 503 },
        ),
      ),
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          { detail: { code: 'worker_unavailable', message: 'сборщик недоступен' } },
          { status: 503 },
        ),
      ),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Обновить сводку рыночных данных' }));

    // Панель заменяется одной строкой — так в макете: счётчики и ленты при
    // потерянной связи показывали бы сбор, которого, может быть, уже нет.
    expect(await screen.findByText('Сборщик недоступен')).toBeInTheDocument();
    expect(screen.getByText(/Ниже показано последнее известное состояние/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Собираем сессию/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Остановить прогон' })).not.toBeInTheDocument();
  });

  it('журнал — только прогоны: без старых пропусков, состава и служебной подписи', async () => {
    // Решение владельца 2026-09-24 (FR-024g): списки «Пропущенные сессии» и
    // «Состав инструментов» показывали сотни давно закрытых записей, а подпись
    // «из журнала сбора, переживает перезапуск» объясняла устройство, а не
    // данные. Причина пропуска остаётся в календаре по дню (FR-002).
    renderWith(
      catchupFixture('finished'),
      [FINISHED_RUN],
      [
        {
          at: '2026-09-17T15:02:41Z',
          ticker: 'SGZH',
          kind: 'opened',
          contract_code: 'SGZH_F',
          detail: 'появился фьючерс SGZH_F',
        },
      ],
      [
        {
          session_date: '2026-09-15',
          reason: 'attempts_exhausted',
          detail: 'три попытки подряд без данных',
          decided_at: '2026-09-17T15:02:41Z',
        },
      ],
    );

    expect(await screen.findByText('Последние прогоны')).toBeInTheDocument();
    expect(screen.queryByText('Пропущенные сессии')).not.toBeInTheDocument();
    expect(screen.queryByText('Состав инструментов')).not.toBeInTheDocument();
    expect(screen.queryByText(/переживает перезапуск/)).not.toBeInTheDocument();
    expect(screen.queryByText('появился фьючерс SGZH_F')).not.toBeInTheDocument();
  });
});

describe('пауза автосбора', () => {
  it('начатый прогон не обрывает, но видна', async () => {
    server.use(http.get('*/api/market-data/settings', () => HttpResponse.json({ paused: true })));

    renderWith(catchupFixture('running'));

    expect(await screen.findByText('Автосбор на паузе')).toBeInTheDocument();
    // Прогон продолжается: кнопка остановки на месте, счётчики живы.
    expect(screen.getByRole('button', { name: 'Остановить прогон' })).toBeInTheDocument();
  });
});

describe('следующий сбор', () => {
  function renderWithCoverage(
    next: string | null,
    blocked: boolean,
    expected: string | null = null,
    awaiting = false,
  ) {
    server.use(
      http.get('*/api/market-data/catchup', () => HttpResponse.json(catchupFixture('finished'))),
      http.get('*/api/market-data/coverage', () =>
        HttpResponse.json(
          coverageFixture({
            next_session: next,
            next_session_blocked: blocked,
            next_expected_session: expected,
            next_expected_awaiting: awaiting,
            calendar_retry_minutes: 15,
          }),
        ),
      ),
    );

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

  it('взять нечего: названа причина, а не расписание', async () => {
    // Пустая дата значит то «соберём по расписанию», то «не возьмём ничего,
    // пока не вмешаетесь». Одна подпись на оба читается как обещание там, где
    // обещания нет (FR-054).
    renderWithCoverage(null, true);

    const line = await waitFor(() => {
      const found = document.querySelector('.run-next');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(line.textContent).toContain('не будет');
    expect(line.textContent).toContain('нужен ручной сбор');
    const schedule = document.querySelector('.schedule-facts') as HTMLElement;
    expect(schedule.textContent).toContain('нет автосбора');
    expect(schedule.textContent).toContain('нужен ручной сбор');
    expect(schedule.textContent).not.toContain('после');
  });

  it('показывает ожидаемую дату сервера под прогоном и календарём', async () => {
    renderWithCoverage(null, false, '2099-01-05');
    await waitFor(() => {
      expect(document.querySelector('.run-next')?.textContent).toContain('05.01.2099');
      expect(document.querySelector('.schedule-facts')?.textContent).toContain('05.01.2099');
    });
    expect(document.querySelector('.run-next')?.textContent).toContain('дата уточняется');
    expect(document.querySelector('.schedule-facts')?.textContent).not.toContain(
      'ближайший торговый день',
    );
  });

  it('после порога без опубликованного дня прошедшее время не обещается', async () => {
    // 23.09.2026 в 23:33 по местному времени строка всё ещё обещала «сегодня
    // после 23:30»: порог прошёл, биржа день не опубликовала (FR-054a).
    renderWithCoverage(null, false, '2099-01-05', true);
    await waitFor(() => {
      expect(document.querySelector('.run-next')?.textContent).toContain('ждём публикации биржей');
    });
    const line = document.querySelector('.run-next')?.textContent ?? '';
    const schedule = document.querySelector('.schedule-facts li strong')?.textContent ?? '';
    for (const text of [line, schedule]) {
      expect(text).toContain('ждём публикации биржей');
      expect(text).toContain('календарь проверяется каждые 15 мин');
      expect(text).not.toMatch(/после \d\d:\d\d/);
    }
  });

  it('старый недобор не заменяет расписание новой сессии', async () => {
    renderWithCoverage('2026-09-21', false, '2099-01-05');
    await waitFor(() => {
      expect(document.querySelector('.run-next b')?.textContent).toContain('05.01.2099');
      expect(document.querySelector('.schedule-facts li strong')?.textContent).toContain(
        '05.01.2099',
      );
    });
  });

  it('исчерпанные повторы не отменяют расписание новых сессий', async () => {
    renderWithCoverage(null, true, '2099-01-05');
    await waitFor(() => {
      expect(document.querySelector('.run-next b')?.textContent).toContain('05.01.2099');
    });
    expect(document.querySelector('.run-next')?.textContent).toContain('исчерпаны попытки');
  });

  it('дата известна: обещание остаётся обещанием', async () => {
    renderWithCoverage('2026-09-17', false);

    const line = await waitFor(() => {
      const found = document.querySelector('.run-next');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(line.textContent).toContain('17.09.2026');
    expect(line.textContent).toMatch(/после \d\d:\d\d/);
  });
});

describe('подпись под шкалой', () => {
  function renderRunning(overrides: Partial<CatchupStateDto['sessions']>) {
    const base = catchupFixture('running');
    renderWith({ ...base, sessions: { ...base.sessions, ...overrides } });
  }

  it('нулей в подписи нет', async () => {
    // «0 пропущены с причинами» — сообщение о том, чего не было, и оно
    // занимает место наравне с настоящими.
    renderRunning({ pending: 5, skipped: 0 });

    const note = await waitFor(() => {
      const found = document.querySelector('.small-note');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(note.textContent).not.toContain('0 пропущены');
    expect(note.textContent).not.toContain('с причинами');
  });

  it('текущая сессия в «дальше ещё» не входит', async () => {
    // Рядом с её датой это читалось как «и ещё столько же сверх неё».
    renderRunning({ pending: 5, skipped: 2 });

    const note = await waitFor(() => {
      const found = document.querySelector('.small-note');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(note.textContent).toContain('дальше ещё 4 сессии');
    expect(note.textContent).toContain('2 пропущены с причинами');
  });

  it('последняя сессия названа последней, а не нулём', async () => {
    renderRunning({ pending: 1, skipped: 0 });

    const note = await waitFor(() => {
      const found = document.querySelector('.small-note');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(note.textContent).toContain('это последняя сессия прогона');
  });
});

describe('журнал прогонов', () => {
  it('проход без сессий назван проверкой календаря, а не сбором из нуля', async () => {
    // 22.09 в 19:30 календарь спросили, новых сессий не было, и журнал писал
    // «0 сессий · 0 собрано · точный итог старой записи недоступен».
    const calendarOnly: RunSummaryDto = {
      ...FINISHED_RUN,
      run_id: 'run-0',
      status: 'finished',
      sessions: { requested: 0, collected: 0, partial: 0, failed: 0, skipped: 0, pending: 0 },
      failures: [],
    };
    renderWith(catchupFixture('idle'), [calendarOnly, FINISHED_RUN]);

    expect(await screen.findByText(/календарь проверен · новых сессий нет/)).toBeInTheDocument();
    expect(screen.queryByText(/0 сессий · 0 собрано/)).not.toBeInTheDocument();
  });
});
