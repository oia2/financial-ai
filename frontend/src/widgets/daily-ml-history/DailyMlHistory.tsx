/**
 * История прогонов: отбор по состоянию, страницы, открытие прогона.
 *
 * Страницы **серверные** (`limit`/`offset`): история растёт по прогону в день и
 * за год станет сотнями строк. Вытягивать её целиком ради показа десяти строк
 * незачем, и отбор по состоянию по той же причине уходит на сервер.
 *
 * Признак устаревания входа в списке не показывается: он требует пересборки
 * набора и считается только при открытии прогона (contracts/…-api.md).
 */

import type { RunRowDto, RunStatus } from '@/entities/daily-ml';
import { formatClock, formatDuration } from '@/shared/lib/daily-ml-format';
import { formatIsoDate } from '@/shared/lib/market-format';
import { Pagination } from '@/shared/ui/Pagination';

import { RunStatusTag } from './RunStatusTag';

const FILTERS: Array<{ value: RunStatus | 'all'; label: string }> = [
  { value: 'all', label: 'Все прогоны' },
  { value: 'failed', label: 'Только отказы' },
  { value: 'success', label: 'Завершённые' },
  { value: 'running', label: 'В работе' },
  { value: 'queued', label: 'Ожидают' },
];

export function DailyMlHistory({
  rows,
  total,
  filter,
  page,
  pageSize,
  onFilterChange,
  onPageChange,
  onPageSizeChange,
  onOpenRun,
  onRetry,
}: {
  rows: RunRowDto[];
  total: number;
  filter: RunStatus | 'all';
  /** Номер страницы, начиная с 1. */
  page: number;
  pageSize: number;
  onFilterChange: (filter: RunStatus | 'all') => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  onOpenRun: (id: number) => void;
  onRetry: (id: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  return (
    <section className="ml-history" data-od-id="ranking-history">
      <div className="ml-section-top">
        <h2>История прогонов</h2>
        <label className="ml-history-filter" htmlFor="mlStatusFilter">
          Статус
          <select
            id="mlStatusFilter"
            data-od-id="ranking-status-filter"
            value={filter}
            onChange={(event) => onFilterChange(event.target.value as RunStatus | 'all')}
          >
            {FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {rows.length === 0 ? (
        <div className="ml-history-empty" data-od-id="empty-ranking-history">
          <h3>{filter === 'all' ? 'Прогонов ещё не было' : 'Нет прогонов с этим статусом'}</h3>
          <p>
            {filter === 'all'
              ? 'Первый прогон появится после готовности обязательного входа. Пропущенные исторические даты долгом не считаются.'
              : 'Выберите другой статус, чтобы увидеть остальные прогоны.'}
          </p>
        </div>
      ) : (
        <>
          <div
            className="ml-table-frame"
            tabIndex={0}
            aria-label="История прогонов"
            data-od-id="ranking-history-table-region"
          >
            <table className="ml-history-table" data-od-id="ranking-history-table">
              <colgroup>
                <col style={{ width: '17%' }} />
                <col style={{ width: '25%' }} />
                <col style={{ width: '25%' }} />
                <col style={{ width: '12%' }} />
                <col style={{ width: '10%' }} />
                <col style={{ width: '11%' }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Дата решения</th>
                  <th>Статус</th>
                  <th>Модель и версия</th>
                  <th>Начало</th>
                  <th>Длительность</th>
                  <th>
                    <span className="sr-only">Действие</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} data-od-id={`ranking-run-${row.id}`}>
                    <td>
                      <button
                        className="date-link"
                        type="button"
                        aria-label={`Открыть прогон за ${formatIsoDate(row.asof_date)}`}
                        data-od-id={`open-ranking-run-${row.id}`}
                        onClick={() => onOpenRun(row.id)}
                      >
                        {formatIsoDate(row.asof_date)}
                      </button>
                    </td>
                    <td>
                      <RunStatusTag status={row.status} />
                      {row.error_message && <p className="ml-error-reason">{row.error_message}</p>}
                      {row.attempt > 1 && (
                        <span className="ml-table-sub">
                          Попытка <span className="mono">{row.attempt}</span>
                        </span>
                      )}
                    </td>
                    <td data-label="Модель">
                      <code>{row.model_id}</code>
                      <span className="ml-table-sub">
                        Версия <span className="mono">{row.model_version}</span>
                      </span>
                    </td>
                    <td data-label="Начало">
                      <time dateTime={row.started_at ?? undefined}>
                        {formatClock(row.started_at)}
                      </time>
                    </td>
                    <td data-label="Длительность">
                      <span className="mono">{formatDuration(row.duration_seconds)}</span>
                    </td>
                    <td>
                      {row.status === 'failed' ? (
                        <button
                          className="ml-inline-button"
                          type="button"
                          data-od-id={`retry-ranking-run-${row.id}`}
                          onClick={() => onRetry(row.id)}
                        >
                          Повторить
                        </button>
                      ) : (
                        <span aria-hidden="true">↗</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pagination
            page={page}
            pageCount={pageCount}
            pageSize={pageSize}
            label="Навигация по истории"
            odId="ranking-history-pagination"
            pageSizeOdId="ranking-history-page-size"
            prevOdId="ranking-history-prev"
            nextOdId="ranking-history-next"
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </>
      )}
    </section>
  );
}
