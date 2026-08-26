import type { PortfolioViewState, SyncDto } from '@/entities/portfolio';
import { formatAge, formatTime } from '@/shared/lib/format';

/**
 * Баннер состояния синхронизации по утверждённому дизайну (FR-037, FR-038).
 *
 * Разметка одна на три причины — как в артефакте: иконка, `banner-copy` и
 * текстовое действие. Различаются формулировки, иконка и модификатор класса;
 * у обрыва связи с сервером — инверсия, чтобы его нельзя было спутать со
 * сбоем брокера (SC-011).
 */
export function SyncStatusBanner({
  state,
  sync,
  ageSeconds,
  onRetry,
  retrying = false,
}: {
  state: PortfolioViewState;
  sync: SyncDto | undefined;
  ageSeconds: number | undefined;
  onRetry: () => void;
  retrying?: boolean;
}) {
  if (state !== 'stale' && state !== 'sync-error' && state !== 'server-offline') {
    return null;
  }

  const lastSuccess = sync?.last_success_at == null ? null : formatTime(sync.last_success_at);
  const age = ageSeconds === undefined ? null : formatAge(ageSeconds);

  const offline = state === 'server-offline';
  const content = offline
    ? {
        title: 'Нет связи с сервером Financial AI',
        text:
          (age === null
            ? 'Данные не загружены.'
            : `Показано последнее известное состояние${lastSuccess === null ? '' : ` на ${lastSuccess}`} — ${age}.`) +
          ' Система автоматически пытается восстановить связь.',
        action: 'Повторить',
      }
    : state === 'sync-error'
      ? {
          title: 'Не удалось обновить портфель',
          text:
            describeBrokerFailure(sync) +
            (lastSuccess === null
              ? ' Успешной синхронизации ещё не было.'
              : ` Показано последнее успешно синхронизированное состояние на ${lastSuccess}` +
                (age === null ? '.' : ` — ${age}.`)),
          action: 'Повторить запрос к брокеру',
        }
      : {
          title: 'Данные временно устарели',
          text:
            (lastSuccess === null
              ? 'Последней успешной синхронизации нет.'
              : `Последняя успешная синхронизация была в ${lastSuccess}.`) +
            (age === null ? '' : ` Возраст данных — ${age}.`) +
            ' Показано последнее известное состояние.',
          action: 'Обновить сейчас',
        };

  return (
    <div
      className={`banner visible${offline ? ' server-offline' : ''}`}
      role="status"
      aria-live="polite"
    >
      {offline ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5v.01M3 3l18 18" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v5M12 16.5v.5" />
        </svg>
      )}

      <div className="banner-copy">
        <strong>{content.title}</strong>
        <span>{content.text}</span>
      </div>

      <button type="button" className="text-button" onClick={onRetry} disabled={retrying}>
        {content.action}
      </button>
    </div>
  );
}

/** Пользователю — понятная формулировка, а не код и не текст брокера (FR-028). */
function describeBrokerFailure(sync: SyncDto | undefined): string {
  switch (sync?.failure_reason_code) {
    case 'rate_limited':
      return 'Брокер временно ограничил частоту запросов.';
    case 'validation_failed':
      return 'Ответ брокера не прошёл проверку, поэтому не был сохранён.';
    case 'broker_rejected_token':
      return 'Брокер отклонил доступ.';
    default:
      return 'T-Bank API не ответил.';
  }
}
