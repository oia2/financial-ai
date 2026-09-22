/**
 * Панель сведений о группе.
 *
 * Те же поля полноты в развёрнутом виде — без единого значения наблюдений
 * (FR-008, FR-017). У группы без истории числовых полей окна нет вовсе, и
 * вместо них дано объяснение неприменимости (FR-014).
 *
 * Семантический `dialog`: он сам держит фокус внутри, закрывается по Escape и
 * возвращает фокус вызвавшей кнопке (FR-053).
 */

import { useEffect, useRef } from 'react';

import type { GroupCoverageDto } from '@/entities/market-data';
import { DASH, formatCount, formatIsoDate, formatRatio } from '@/shared/lib/market-format';

import { capitalize } from './GroupsSection';

export function GroupDetailsDrawer({
  group,
  onClose,
}: {
  group: GroupCoverageDto | null;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;

    if (group !== null && !dialog.open) dialog.showModal();
    if (group === null && dialog.open) dialog.close();
  }, [group]);

  return (
    <dialog className="drawer" ref={ref} onClose={onClose} aria-labelledby="groupDrawerTitle">
      <div className="drawer-heading">
        <h2 id="groupDrawerTitle">{group === null ? 'Группа данных' : capitalize(group.title)}</h2>
        <button
          className="icon-button"
          type="button"
          aria-label="Закрыть сведения о группе"
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <div className="drawer-body">
        {group !== null && (
          <>
            <p className="mono">{group.group}</p>

            {!group.has_history && (
              <p>
                Справочник текущего состояния. История не ведётся; окно и покрытие к этой группе не
                применяются.
              </p>
            )}

            <dl className="detail-metrics">
              {group.has_history && (
                <>
                  <Metric label="Окно" value={`${group.window_sessions} сессий`} />
                  <Metric label="Покрыто сессий" value={String(group.sessions_covered)} />
                  <Metric label="Покрытие" value={formatRatio(group.coverage_ratio)} />
                  <Metric label="Не покрыто" value={formatCount(group.gaps)} />
                </>
              )}

              {(group.requires_audit ?? 0) > 0 && (
                <Metric
                  label={
                    group.has_history ? 'Сессий требуют аудита' : 'Источников требуют проверки'
                  }
                  value={String(group.requires_audit)}
                />
              )}

              <Metric label="Строк со значениями" value={formatRatio(group.value_ratio)} />
              <Metric label="Всего строк" value={formatCount(group.rows_total)} />
              <Metric
                label="Строк со значениями, шт."
                value={formatCount(group.rows_with_values)}
              />

              {group.has_history && (
                <Metric
                  label="Период данных"
                  value={`${formatIsoDate(group.period_from)} — ${formatIsoDate(group.period_till)}`}
                />
              )}
            </dl>

            <p className="field-help">Прочерк ({DASH}) означает отсутствие значения, а не ноль.</p>
          </>
        )}
      </div>

      <div className="drawer-footer">
        <button className="secondary-button" type="button" onClick={onClose}>
          Закрыть
        </button>
      </div>
    </dialog>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
