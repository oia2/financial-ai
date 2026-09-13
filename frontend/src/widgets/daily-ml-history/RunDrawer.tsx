/**
 * Просмотр одного прогона: вход, исход, результат.
 *
 * Четыре утверждения, без которых панель была бы опасной:
 *
 *  - **скоры эмулятора вымышлены** (FR-049). Предупреждение стоит на самом
 *    результате, а не в сноске внизу страницы;
 *  - **скор задаёт порядок внутри даты** (FR-050). Между датами он не
 *    сравнивается и доходности не означает;
 *  - **набор мог быть удалён ретеншеном** (FR-040). Результат остаётся, повтор
 *    становится невозможен — и кнопка повтора выключается, а не исчезает молча;
 *  - **вход мог измениться после прогона** (FR-023–FR-025). Для последней
 *    актуальной даты пересчёт идёт сам, для исторической — не идёт, и это
 *    сказано прямо.
 *
 * Семантический `dialog`: держит фокус внутри, закрывается по Escape и
 * возвращает фокус вызвавшему элементу (FR-072).
 */

import { useEffect, useRef, useState } from 'react';

import type { RunDetailDto } from '@/entities/daily-ml';
import { formatDuration, formatStamp } from '@/shared/lib/daily-ml-format';
import { DASH, formatCount, formatIsoDate } from '@/shared/lib/market-format';
import { Pagination } from '@/shared/ui/Pagination';

import { RunStatusTag } from './RunStatusTag';

const RANK_PAGE_SIZES = [10, 25, 50];

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div data-od-id={`run-field-${id}`}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function withoutResult(status: RunDetailDto['status']) {
  if (status === 'failed') {
    return {
      title: 'Результат не получен',
      text: 'Прежние результаты доступны в истории. Причина отказа указана в исходе этого прогона.',
    };
  }
  return {
    title: status === 'running' ? 'Выполняется ранжирование' : 'Прогон ожидает обработки',
    text: 'Порядок активов появится после получения ответа.',
  };
}

export function RunDrawer({
  run,
  latestDataReady,
  onClose,
  onRetry,
  onOpenPlan,
}: {
  run: RunDetailDto | null;
  /** Последняя дата с готовыми данными: от неё зависит, будет ли пересчёт. */
  latestDataReady: string | null;
  onClose: () => void;
  onRetry: (id: number) => void;
  onOpenPlan: (id: number) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [rankPage, setRankPage] = useState(1);
  const [rankPageSize, setRankPageSize] = useState(25);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;

    if (run !== null && !dialog.open) dialog.showModal();
    if (run === null && dialog.open) dialog.close();
  }, [run]);

  const runId = run?.id ?? null;
  useEffect(() => {
    setRankPage(1);
  }, [runId]);

  const items = run?.items ?? [];
  const pageCount = Math.max(1, Math.ceil(items.length / rankPageSize));
  const visible = items.slice((rankPage - 1) * rankPageSize, rankPage * rankPageSize);

  return (
    <dialog
      className="drawer ml-run-drawer"
      ref={ref}
      onClose={onClose}
      aria-labelledby="mlRunTitle"
      data-od-id="ranking-run-drawer"
    >
      <div className="drawer-heading">
        <div>
          <h2 id="mlRunTitle">
            {run === null
              ? 'Прогон'
              : `${run.status === 'success' ? 'Результат за ' : 'Прогон за '}`}
            {run !== null && <span className="mono">{formatIsoDate(run.asof_date)}</span>}
          </h2>
          <p>
            {run !== null && (
              <>
                <RunStatusTag status={run.status} />
                {run.attempt > 1 && (
                  <>
                    {' '}
                    · попытка <span className="mono">{run.attempt}</span>
                  </>
                )}
              </>
            )}
          </p>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="Закрыть прогон"
          data-od-id="close-ranking-run"
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <div className="drawer-body">
        {run !== null && (
          <>
            <section className="ml-result-section" data-od-id="run-result">
              <div className="ml-result-header">
                <h3>Результат ранжирования</h3>
                {run.status === 'success' && (
                  <span className="ml-result-count" data-od-id="ranking-asset-count">
                    Активов: <span className="mono">{formatCount(run.included_asset_count)}</span>
                  </span>
                )}
              </div>

              {run.status === 'success' ? (
                <>
                  {run.emulated && (
                    <div className="ml-emulation-warning" data-od-id="emulated-score-warning">
                      <strong>Скоры вымышлены эмулятором</strong>
                      <p>
                        Скор задаёт порядок только внутри этой даты. Между датами он не сравнивается
                        и доходности не означает. Ранжирование не исполняет сделки.
                      </p>
                    </div>
                  )}

                  <div
                    className="ml-ranking-scroll"
                    tabIndex={0}
                    aria-label="Ранжирование активов"
                    data-od-id="ranking-result-scroll"
                  >
                    <table className="ml-ranking-table" data-od-id="ranking-result-table">
                      <colgroup>
                        <col style={{ width: '9%' }} />
                        <col style={{ width: '36%' }} />
                        <col style={{ width: '36%' }} />
                        <col style={{ width: '19%' }} />
                      </colgroup>
                      <thead>
                        <tr>
                          <th>Место</th>
                          <th>Актив</th>
                          <th>Ценовой ряд</th>
                          <th>Скор</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visible.map((item) => (
                          <tr key={item.rank} data-od-id={`ranking-item-${item.rank}`}>
                            <td>{item.rank}</td>
                            <td>{item.asset_id}</td>
                            <td>{item.price_series_id}</td>
                            {/* Скор приходит строкой и строкой же показывается:
                                разбор в число изменил бы последний разряд, а он
                                участвует в порядке. */}
                            <td className="score">{item.score.replace('.', ',')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <Pagination
                    page={rankPage}
                    pageCount={pageCount}
                    pageSize={rankPageSize}
                    sizes={RANK_PAGE_SIZES}
                    label="Навигация по ранжированию"
                    odId="ranking-result-pagination"
                    pageSizeOdId="ranking-result-page-size"
                    prevOdId="ranking-result-prev"
                    nextOdId="ranking-result-next"
                    onPageChange={setRankPage}
                    onPageSizeChange={(size) => {
                      setRankPageSize(size);
                      setRankPage(1);
                    }}
                  />
                </>
              ) : (
                <div className="ml-history-empty" data-od-id="run-without-result">
                  <h3>{withoutResult(run.status).title}</h3>
                  <p>{withoutResult(run.status).text}</p>
                </div>
              )}
            </section>

            {run.status === 'success' && (
              <div className="ml-plan-link">
                <p>Рассчитать изменения текущего портфеля по этому ранжированию.</p>
                <button
                  className="secondary-button"
                  type="button"
                  data-od-id="plan-from-ranking-result"
                  onClick={() => onOpenPlan(run.id)}
                >
                  Открыть план портфеля →
                </button>
              </div>
            )}

            {!run.input.dataset_available && (
              <div className="ml-retention-note" data-od-id="retained-ranking-deleted-input">
                <strong>Входной набор удалён после срока хранения</strong>
                <p>
                  Срок хранения — <span className="mono">30 дней</span>. Ранжирование осталось в
                  истории. Пересчёт по этому набору уже невозможен.
                </p>
              </div>
            )}

            {run.stale && (
              <div className="ml-retention-note" data-od-id="stale-run-input">
                <strong>После прогона вход изменился</strong>
                <p>
                  {run.asof_date === latestDataReady
                    ? 'Для последней актуальной даты пересчёт выполняется автоматически.'
                    : 'Это историческая дата. Автоматического пересчёта для неё нет.'}
                </p>
              </div>
            )}

            <details
              className="ml-run-metadata"
              data-od-id="run-metadata"
              open={run.status !== 'success'}
            >
              <summary data-od-id="open-run-metadata">Входные данные и сведения о прогоне</summary>
              <div className="ml-detail-grid">
                <section className="ml-detail-block" data-od-id="run-input">
                  <h3>Вход</h3>
                  <dl className="ml-detail-list">
                    <Field id="decision-date" label="Дата решения">
                      <span className="mono">{formatIsoDate(run.asof_date)}</span>
                    </Field>
                    <Field id="input-digest" label="Дайджест">
                      <code>{run.input.dataset_digest}</code>
                    </Field>
                    <Field id="input-dataset" label="Набор">
                      {run.input.dataset_available ? (
                        <code>{run.input.dataset_ref}</code>
                      ) : (
                        'Набор удалён'
                      )}
                    </Field>
                    <Field id="model-id" label="Модель">
                      <code>{run.model_id}</code>
                    </Field>
                    <Field id="model-version" label="Версия">
                      <span className="mono">{run.model_version}</span>
                    </Field>
                    <Field id="input-window" label="Окно данных">
                      <span className="mono">{formatIsoDate(run.input.window_from)}</span>
                      {' — '}
                      <span className="mono">{formatIsoDate(run.input.window_till)}</span>
                    </Field>
                    <Field id="input-completeness" label="Полнота входа">
                      {run.input.complete === null
                        ? DASH
                        : run.input.complete
                          ? 'Обязательный вход объявлен полным'
                          : 'Обязательный вход неполон'}
                    </Field>
                  </dl>
                </section>

                <section className="ml-detail-block" data-od-id="run-outcome">
                  <h3>Исход</h3>
                  <dl className="ml-detail-list">
                    <Field id="status" label="Статус">
                      <RunStatusTag status={run.status} />
                    </Field>
                    <Field id="started-at" label="Начало">
                      <span className="mono">{formatStamp(run.started_at)}</span>
                    </Field>
                    <Field id="finished-at" label="Окончание">
                      <span className="mono">{formatStamp(run.finished_at)}</span>
                    </Field>
                    <Field id="duration" label="Длительность">
                      <span className="mono">{formatDuration(run.duration_seconds)}</span>
                    </Field>
                    <Field id="attempt" label="Попытка">
                      <span className="mono">{run.attempt}</span>
                    </Field>
                    {run.error_message && (
                      <Field id="failure-reason" label="Причина отказа">
                        {run.error_message}
                      </Field>
                    )}
                  </dl>
                </section>
              </div>
            </details>
          </>
        )}
      </div>

      <div className="drawer-footer">
        <button
          className="secondary-button"
          type="button"
          data-od-id="close-ranking-run-footer"
          onClick={onClose}
        >
          Закрыть
        </button>
        {run !== null && run.status === 'failed' && (
          <button
            className="primary-button"
            type="button"
            data-od-id="retry-viewed-run"
            // Набор удалён — повторять нечем. Кнопка остаётся видимой и
            // выключенной: исчезнувшая кнопка не объясняет, почему повтор
            // невозможен, а причина названа выше.
            disabled={!run.input.dataset_available}
            onClick={() => onRetry(run.id)}
          >
            Повторить
          </button>
        )}
      </div>
    </dialog>
  );
}
