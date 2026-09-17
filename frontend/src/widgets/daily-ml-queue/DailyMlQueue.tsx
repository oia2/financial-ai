/**
 * Очередь дат, принятых к обработке.
 *
 * Два утверждения, ради которых блок существует:
 *
 *  - даты выполняются **по одной**, от старой к новой. Очередь — это не набор
 *    параллельных работ, и порядок здесь значим;
 *  - прогресс **счётный**, а не долевой: «обработано N из M». Доли готовности
 *    внутри одной даты нет — её никто не сообщает, и полоса по таймеру
 *    браузера была бы выдумкой (ui-states.md, раздел «Прогресс»).
 *
 * Идущая дата из перечня исключается: она уже показана отдельно, в блоке
 * текущего прогона, и повтор читался бы как две разные работы.
 */

import type { DailyMlStatusDto } from '@/entities/daily-ml';
import { formatCount, formatIsoDate } from '@/shared/lib/market-format';

const STATUS_NAMES: Record<string, string> = {
  queued: 'Ожидает',
  running: 'В работе',
  success: 'Завершён',
  failed: 'Отказ',
};

export function DailyMlQueue({ status }: { status: DailyMlStatusDto }) {
  const pending = status.queue
    .filter((item) => item.asof_date !== status.current?.asof_date)
    .slice()
    .sort((first, second) => first.asof_date.localeCompare(second.asof_date));

  const { completed, total } = status.queue_progress;
  if (pending.length === 0 && total === 0) return null;

  const remaining = total - completed;

  return (
    <div className="ml-pending" data-od-id="ranking-pending-dates">
      {total > 0 && (
        <p className="ml-queue-progress" role="status" data-od-id="ranking-queue-progress">
          Обработано <span className="mono">{formatCount(completed)}</span> из{' '}
          <span className="mono">{formatCount(total)}</span> ·{' '}
          {remaining === 0 ? (
            'все даты очереди обработаны'
          ) : (
            <>
              осталось <span className="mono">{formatCount(remaining)}</span>
              {status.current ? ' (включая текущую)' : ''}
            </>
          )}
        </p>
      )}
      {pending.length > 0 && (
        <>
          <p className="ml-pending-label">Ожидают обработки · выполняются по одной</p>
          <ol className="ml-queue-list">
            {pending.map((item) => (
              <li key={item.asof_date} data-od-id={`queued-date-${item.asof_date}`}>
                <time dateTime={item.asof_date}>{formatIsoDate(item.asof_date)}</time>
                <span className={`ml-run-status ${item.status}`}>
                  {STATUS_NAMES[item.status] ?? item.status}
                </span>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
