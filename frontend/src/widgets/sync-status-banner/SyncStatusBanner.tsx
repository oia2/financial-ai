import type { PortfolioViewState, SyncDto } from '@/entities/portfolio';
import { formatAge, formatTime } from '@/shared/lib/format';

import './sync-status-banner.css';

/**
 * Предупреждения о несвежести данных (FR-037, FR-038).
 *
 * Три причины показываются по-разному и не могут быть перепутаны:
 *
 *  - данные устарели — брокер отвечает, но синхронизации давно не было;
 *  - не удалось обновить портфель — T-Bank API недоступен или вернул ошибку;
 *  - нет связи с сервером Financial AI — до самого сервера не достучаться.
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

  if (state === 'server-offline') {
    return (
      <section className="banner banner-offline" role="status">
        <div className="banner-copy">
          <strong>Нет связи с сервером Financial AI</strong>
          <span>
            {age === null
              ? 'Данные не загружены.'
              : `Показано последнее известное состояние${
                  lastSuccess === null ? '' : ` на ${lastSuccess}`
                } — ${age}.`}{' '}
            Система автоматически пытается восстановить связь.
          </span>
        </div>
        <button type="button" className="banner-action" onClick={onRetry} disabled={retrying}>
          Повторить
        </button>
      </section>
    );
  }

  if (state === 'sync-error') {
    return (
      <section className="banner banner-error" role="status">
        <div className="banner-copy">
          <strong>Не удалось обновить портфель</strong>
          <span>
            {describeBrokerFailure(sync)}
            {lastSuccess === null
              ? ' Успешной синхронизации ещё не было.'
              : ` Показано последнее успешно синхронизированное состояние на ${lastSuccess}.`}
          </span>
        </div>
        <button type="button" className="banner-action" onClick={onRetry} disabled={retrying}>
          Повторить запрос к брокеру
        </button>
      </section>
    );
  }

  return (
    <section className="banner banner-stale" role="status">
      <div className="banner-copy">
        <strong>Данные временно устарели</strong>
        <span>
          {lastSuccess === null
            ? 'Последней успешной синхронизации нет.'
            : `Последняя успешная синхронизация была в ${lastSuccess}.`}{' '}
          {age === null ? '' : `Возраст данных — ${age}.`} Показано последнее известное состояние.
        </span>
      </div>
      <button type="button" className="banner-action" onClick={onRetry} disabled={retrying}>
        Обновить сейчас
      </button>
    </section>
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
