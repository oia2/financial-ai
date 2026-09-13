/**
 * Состояние раздела «Ранжирование».
 *
 * Восемь состояний одним компонентом, по разметке артефакта Open Design
 * (`daily-ml.html`). Правила, которые здесь важнее вёрстки:
 *
 *  - **две даты, а не одна** (FR-047). Готовность данных и готовность модели —
 *    разные величины, и именно их расхождение показывает отставание;
 *  - **процента готовности внутри даты нет** (FR-072). Сервер его не сообщает,
 *    а полоса по таймеру браузера была бы выдумкой. Идущее время — факт, и оно
 *    показывается; доля выполненного — нет;
 *  - **ожидаемая длительность это оценка**, а не срок окончания, и текст обязан
 *    так и звучать;
 *  - **при паузе сказано, что сбор данных продолжается** (FR-055): пауза
 *    останавливает ранжирование, а не сбор, и смешение этих двух вещей — самая
 *    дорогая ошибка чтения этого экрана.
 */

import type { DailyMlStatusDto, SectionStatus } from '@/entities/daily-ml';
import { formatClock, formatDurationReference, formatSeconds } from '@/shared/lib/daily-ml-format';
import { DASH, formatCount, formatIsoDate } from '@/shared/lib/market-format';

/** Восьмое состояние — устаревший вход — приходит признаком, а не статусом. */
type StateName = SectionStatus | 'stale';

const HEADINGS: Record<StateName, string> = {
  up_to_date: 'Ранжирование выполнено',
  waiting: 'Ожидание данных',
  running: 'Идёт ранжирование',
  lagging: 'Ранжирование отстаёт',
  failed: 'Последний прогон не удался',
  paused: 'Автоматическое ранжирование остановлено',
  data_gap: 'Разрыв данных',
  stale: 'Входные данные изменились',
};

export function stateName(status: DailyMlStatusDto): StateName {
  // Идущий прогон называется своим именем даже на паузе: пауза запрещает новые
  // задания, а начатое доводится до конца.
  if (status.current) return 'running';
  if (status.stale_latest && (status.status === 'up_to_date' || status.status === 'waiting')) {
    return 'stale';
  }
  return status.status;
}

function explanation(status: DailyMlStatusDto): { text: React.ReactNode; error: boolean } {
  const name = stateName(status);

  if (name === 'data_gap') {
    return {
      error: false,
      text: (
        <>
          <strong>
            Данные отстают на {formatCount(status.data_gap_sessions)} торговых сессий.
          </strong>{' '}
          Автоматическое восстановление не выполнено. Сначала восстановите полноту входа.
        </>
      ),
    };
  }

  if (name === 'failed') {
    return {
      error: true,
      text: (
        <>
          <strong>{status.last_error ?? 'Прогон не выполнен.'}</strong> Предыдущие результаты
          сохранены.
        </>
      ),
    };
  }

  if (status.paused) {
    return {
      error: false,
      text: (
        <>
          <strong>Сбор рыночных данных продолжается.</strong> Автоматические запуски ранжирования
          выключены.{' '}
          {status.current ? 'Текущий прогон завершится.' : 'Ручной повтор отказа доступен.'}
        </>
      ),
    };
  }

  if (name === 'stale') {
    return {
      error: false,
      text: (
        <>
          <strong>Последний результат использует прежний вход.</strong> Для последней актуальной
          даты пересчёт выполняется автоматически. Исторические даты автоматически не
          пересчитываются.
        </>
      ),
    };
  }

  if (name === 'running') {
    return {
      error: false,
      text: (
        <>
          <strong>Рассчитывается порядок активов.</strong> Результат появится здесь после
          завершения.
        </>
      ),
    };
  }

  if (name === 'lagging') {
    return {
      error: false,
      text: (
        <>
          <strong>Данные готовы, ранжирование отстаёт.</strong> Ниже — даты, принятые к обработке;
          они выполняются по одной. Пропуски в истории заданиями не становятся.
        </>
      ),
    };
  }

  if (name === 'up_to_date') {
    return {
      error: false,
      text: (
        <>
          <strong>Данные и ранжирование на одной дате.</strong> После последнего прогона новых
          закрытых сессий не появилось.
        </>
      ),
    };
  }

  // Ожидание данных объясняется до конца: «ожидаются данные» без ответа
  // «каких?» оставляет человека гадать (US1/AC5).
  const blocking = status.blocking_groups.map((item) => item.title).join(', ');

  return {
    error: false,
    text: (
      <>
        <strong>Ожидаются данные закрытой торговой сессии.</strong>{' '}
        {blocking ? `Не хватает: ${blocking}.` : ''} Время готовности заранее неизвестно.
      </>
    ),
  };
}

export function DailyMlState({
  status,
  latestRunId,
  lastFailedRunId,
  recentDurations,
  runningSeconds,
  onOpenRun,
  onRetry,
  onOpenMarketData,
}: {
  status: DailyMlStatusDto;
  /** Прогон, давший последний результат; `null` — результата ещё нет. */
  latestRunId: number | null;
  /** Последний отказавший прогон: повтор возможен только по нему. */
  lastFailedRunId: number | null;
  /** Длительности последних успешных прогонов — для оценки, не для обещания. */
  recentDurations: number[];
  /** Сколько идёт текущий прогон, секунд. Считается от `started_at`. */
  runningSeconds: number | null;
  onOpenRun: (id: number) => void;
  onRetry: (id: number) => void;
  onOpenMarketData: () => void;
}) {
  const name = stateName(status);
  const message = explanation(status);
  const reference = formatDurationReference(recentDurations);

  const modelNote = status.stale_latest
    ? 'Результат по прежнему входу'
    : status.latest_ml_success
      ? 'Последний завершённый прогон'
      : 'Прогонов пока не было';

  return (
    <section
      className="ml-state"
      data-od-id="ranking-state-and-controls"
      aria-label="Состояние данных и ранжирования"
    >
      <div className="ml-state-head">
        <h2 data-od-id="ranking-state-title">{HEADINGS[name]}</h2>
        <span className="ml-state-caption">
          <code>{status.model_id ?? 'модель не известна'}</code>
          <br />
          Версия: <span className="mono">{status.model_version ?? 'не передана'}</span>
        </span>
      </div>

      <div className="ml-dates">
        <div className="ml-date-cell" data-od-id="latest-data-ready">
          <span className="label">Данные готовы по</span>
          <strong className="ml-date-value">{formatIsoDate(status.latest_data_ready)}</strong>
          <span className="ml-date-note">
            {/* «Готовой даты нет» и «о готовности сказать нечего» — разные
                утверждения, и второе не выдаётся за первое. */}
            {!status.readiness_known
              ? 'Сборщик не ответил: готовность неизвестна'
              : status.latest_data_ready
                ? 'Полный обязательный вход'
                : 'Готовый вход пока отсутствует'}
          </span>
        </div>
        <div className="ml-date-cell" data-od-id="latest-ranking-success">
          <span className="label">Ранжирование выполнено по</span>
          <strong className="ml-date-value">{formatIsoDate(status.latest_ml_success)}</strong>
          <span className="ml-date-note">{modelNote}</span>
        </div>
        <div className="ml-date-cell ml-latest-result" data-od-id="latest-ranking-result">
          <span className="label">Последний результат</span>
          {latestRunId !== null ? (
            <>
              <button
                className="primary-button"
                type="button"
                data-od-id="open-latest-ranking-result"
                aria-label={`Открыть результат за ${formatIsoDate(status.latest_ml_success)}`}
                onClick={() => onOpenRun(latestRunId)}
              >
                Открыть результат <span aria-hidden="true">↗</span>
              </button>
              <span className="ml-date-note">
                Порядок активов и скоры за{' '}
                <span className="mono">{formatIsoDate(status.latest_ml_success)}</span>
              </span>
            </>
          ) : (
            <>
              <strong className="ml-result-pending">Результата пока нет</strong>
              <span className="ml-date-note">Появится после первого завершённого прогона</span>
            </>
          )}
        </div>
      </div>

      <div
        className={`ml-state-message${message.error ? ' is-error' : ''}`}
        data-od-id="ranking-state-explanation"
      >
        <p>{message.text}</p>
        {name === 'failed' && lastFailedRunId !== null && (
          <button
            className="ml-inline-button"
            type="button"
            data-od-id="retry-last-failed-run"
            onClick={() => onRetry(lastFailedRunId)}
          >
            Повторить
          </button>
        )}
        {name === 'data_gap' && (
          <a
            href="#market-data"
            data-od-id="resolve-data-gap"
            onClick={(event) => {
              event.preventDefault();
              onOpenMarketData();
            }}
          >
            Открыть рыночные данные →
          </a>
        )}
      </div>

      {status.current && (
        <div className="ml-current" data-od-id="current-inference">
          <span className="process-glyph" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>Ранжирование за</span>
          <time dateTime={status.current.asof_date}>{formatIsoDate(status.current.asof_date)}</time>
          <span className="ml-current-meta">
            Начало <span className="mono">{formatClock(status.current.started_at)}</span> · идёт{' '}
            <span className="ml-duration">
              {runningSeconds === null ? DASH : formatSeconds(runningSeconds)}
            </span>
            {status.current.attempt > 1 && (
              <>
                {' '}
                · попытка <span className="mono">{status.current.attempt}</span>
              </>
            )}
            {/* Оценка по прошлым прогонам. Пока прогонов меньше двух, здесь
                пусто: одно наблюдение не даёт разброса, и показывать его как
                ориентир значило бы обещать срок. */}
            {reference && (
              <span className="ml-timing-history" data-od-id="inference-duration-history">
                {reference}
              </span>
            )}
          </span>
        </div>
      )}
    </section>
  );
}
