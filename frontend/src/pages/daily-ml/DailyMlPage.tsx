/**
 * Раздел «Ранжирование».
 *
 * Жизненный цикл Daily ML: состояние, управление, очередь, история прогонов и
 * просмотр отдельного прогона. Композиция перенесена из артефакта Open Design
 * (`daily-ml.html`).
 *
 * Три решения, принятые здесь, а не в виджетах:
 *
 *  - **идущее время считается в браузере, доля выполненного — нет.** Прошедшее
 *    с `started_at` это факт, и тикающий счётчик его не выдумывает. Процента
 *    готовности нет ни в ответе сервера, ни на экране (FR-072);
 *  - **последний результат ищется по истории**, а не по дате из состояния:
 *    открыть можно прогон, а не дату, и его идентификатор знает только список;
 *  - **отдельный запрос за длительностями** последних успешных прогонов:
 *    ожидаемая длительность строится по ним и остаётся оценкой, не обещанием.
 */

import { useEffect, useMemo, useState } from 'react';

import { navigate } from '@/app/router';
import { useDailyMlStatus, useRunDetail, useRunHistory, type RunStatus } from '@/entities/daily-ml';
import { DailyMlControls } from '@/features/daily-ml-control/DailyMlControls';
import { useDailyMlControl } from '@/features/daily-ml-control/useDailyMlControl';
import { DailyMlHistory } from '@/widgets/daily-ml-history/DailyMlHistory';
import { RunDrawer } from '@/widgets/daily-ml-history/RunDrawer';
import { DailyMlQueue } from '@/widgets/daily-ml-queue/DailyMlQueue';
import { AutomationDetails } from '@/widgets/daily-ml-state/AutomationDetails';
import { DailyMlState } from '@/widgets/daily-ml-state/DailyMlState';

/** Сколько завершённых прогонов берётся для оценки длительности. */
const REFERENCE_RUNS = 20;

export function DailyMlPage() {
  const status = useDailyMlStatus();
  const control = useDailyMlControl();

  const [filter, setFilter] = useState<RunStatus | 'all'>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [openRunId, setOpenRunId] = useState<number | null>(null);

  const history = useRunHistory(filter, pageSize, (page - 1) * pageSize);
  const successes = useRunHistory('success', REFERENCE_RUNS, 0);
  const failures = useRunHistory('failed', 1, 0);
  const detail = useRunDetail(openRunId);

  const durations = useMemo(
    () =>
      (successes.data?.items ?? [])
        .map((run) => run.duration_seconds)
        .filter((value): value is number => value !== null),
    [successes.data],
  );

  const latestRunId = successes.data?.items[0]?.id ?? null;
  const lastFailedRunId = failures.data?.items[0]?.id ?? null;

  const startedAt = status.data?.current?.started_at ?? null;
  const runningSeconds = useElapsedSeconds(startedAt);

  // Смена отбора возвращает к первой странице: иначе третья страница отказов
  // открылась бы пустой при переходе на «все прогоны».
  function changeFilter(next: RunStatus | 'all') {
    setFilter(next);
    setPage(1);
  }

  if (status.data === undefined) {
    return (
      <main className="ml-main" data-od-id="daily-ml-main">
        <div className="page-heading ml-heading" data-od-id="ranking-page-heading">
          <div>
            <p className="ml-eyebrow">Daily ML</p>
            <h1 data-od-id="ranking-heading">Ранжирование</h1>
          </div>
        </div>
        <p role="status">
          {status.isError ? 'Состояние ранжирования недоступно.' : 'Читаем состояние…'}
        </p>
      </main>
    );
  }

  const state = status.data;

  return (
    <main className="ml-main" data-od-id="daily-ml-main">
      <div className="page-heading ml-heading" data-od-id="ranking-page-heading">
        <div>
          <p className="ml-eyebrow">Daily ML</p>
          <h1 data-od-id="ranking-heading">Ранжирование</h1>
        </div>
        <DailyMlControls
          paused={state.paused}
          busy={false}
          onTogglePause={() => control.togglePause(!state.paused)}
          onCheckNow={control.checkNow}
        />
      </div>

      {control.notice && (
        <p
          className="ml-notice-live"
          role="status"
          aria-live="polite"
          data-od-id="ranking-action-response"
        >
          {control.notice}
        </p>
      )}

      <DailyMlState
        status={state}
        latestRunId={latestRunId}
        lastFailedRunId={lastFailedRunId}
        recentDurations={durations}
        runningSeconds={runningSeconds}
        onOpenRun={setOpenRunId}
        onRetry={control.retryRun}
        onOpenMarketData={() => navigate('market-data')}
      />

      <DailyMlQueue status={state} />

      <AutomationDetails status={state} />

      <DailyMlHistory
        rows={history.data?.items ?? []}
        total={history.data?.total ?? 0}
        filter={filter}
        page={page}
        pageSize={pageSize}
        onFilterChange={changeFilter}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        onOpenRun={setOpenRunId}
        onRetry={control.retryRun}
      />

      <RunDrawer
        run={detail.data ?? null}
        latestDataReady={state.latest_data_ready}
        onClose={() => setOpenRunId(null)}
        onRetry={(id) => {
          control.retryRun(id);
          setOpenRunId(null);
        }}
        onOpenPlan={() => navigate('portfolio-plan')}
      />
    </main>
  );
}

/**
 * Сколько времени идёт прогон.
 *
 * Это прошедшее время, а не доля выполненного: секунды считаются от момента
 * начала, известного серверу. Сколько осталось, никто не знает, и таймер об
 * этом ничего не утверждает.
 */
function useElapsedSeconds(startedAt: string | null): number | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (startedAt === null) return;

    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  if (startedAt === null) return null;
  return Math.max(0, (now - Date.parse(startedAt)) / 1000);
}
