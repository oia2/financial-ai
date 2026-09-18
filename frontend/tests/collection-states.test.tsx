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
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { CatchupStateDto, LinkEventDto, RunsDto, RunSummaryDto } from '@/entities/market-data';

import { catchupFixture } from './msw/market-data';
import { http, HttpResponse, server } from './msw/server';

function renderWith(
  state: CatchupStateDto,
  runs: RunSummaryDto[] = [],
  events: LinkEventDto[] = [],
  skips: RunsDto['skips'] = [],
) {
  server.use(
    http.get('*/api/market-data/catchup', () => HttpResponse.json(state)),
    http.get('*/api/market-data/runs', () => HttpResponse.json({ runs, events, skips })),
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
  sessions: { requested: 12, collected: 10, failed: 1, skipped: 1 },
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

  it('идёт: календарь помечен суточным и в счёт сессии не входит', async () => {
    renderWith(catchupFixture('running'));

    expect(await screen.findByText('раз в сутки')).toBeInTheDocument();
    // Посессионных источников три, отработал один: календарь в счёт не идёт.
    expect(document.querySelector('.rail-head b')?.textContent).toContain('1 из 3');
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

  it('причина пропуска переживает перезапуск сборщика', async () => {
    // Ход прогона живёт в памяти и исчезает вместе с процессом; причина
    // приходит из хранилища, поэтому остаётся на экране (FR-002, SC-002).
    renderWith(
      catchupFixture('idle', { sessions: { ...catchupFixture('idle').sessions, requested: 0 } }),
      [FINISHED_RUN],
      [],
      [
        {
          session_date: '2026-09-15',
          reason: 'attempts_exhausted',
          detail: 'три попытки подряд без данных',
          decided_at: '2026-09-17T15:02:41Z',
        },
      ],
    );

    expect(await screen.findByText('Пропущенные сессии')).toBeInTheDocument();
    expect(screen.getByText('исчерпан предел попыток')).toBeInTheDocument();
    expect(screen.getByText('три попытки подряд без данных')).toBeInTheDocument();
  });

  it('изменение состава инструментов названо, а не спрятано в числах', async () => {
    // Иначе рост или убыль числа собранных бумаг выглядели бы пропуском
    // сбора, а не появлением и исчезновением инструментов (FR-016).
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
    );

    expect(await screen.findByText('Состав инструментов')).toBeInTheDocument();
    expect(screen.getByText('SGZH')).toBeInTheDocument();
    expect(screen.getByText('появился фьючерс SGZH_F')).toBeInTheDocument();
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
