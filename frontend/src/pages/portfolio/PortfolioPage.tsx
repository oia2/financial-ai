import {
  selectPortfolioState,
  usePortfolioQuery,
  type PortfolioDto,
  type PortfolioViewState,
} from '@/entities/portfolio';
import { AppHeader } from '@/widgets/app-header/AppHeader';
import { CapitalStrip } from '@/widgets/capital-strip/CapitalStrip';
import { PositionsSection } from '@/widgets/positions-section/PositionsSection';
import { SyncStatusBanner } from '@/widgets/sync-status-banner/SyncStatusBanner';

/**
 * Раздел «Портфель».
 *
 * Композиция повторяет утверждённый дизайн Open Design (FR-017): шапка,
 * заголовок со строкой подключения, баннер состояния, полоса капитала,
 * секция позиций. Все состояния (FR-015) выводятся из одного ответа API и
 * факта его наличия — страница ничего не вычисляет сама.
 */
export function PortfolioPage() {
  const query = usePortfolioQuery();
  const state = selectPortfolioState(query.data, query.error, query.isPending);

  return (
    <div className="app-shell">
      <AppHeader data={query.data} />

      <main>
        {state === 'loading' ? (
          <div className="skeleton-shell" aria-live="polite">
            <div className="skeleton-line skeleton-heading" />
            <div className="skeleton-block skeleton-summary" />
            <div className="skeleton-line skeleton-row" />
            <div className="skeleton-line skeleton-row" />
            <div className="skeleton-line skeleton-row" />
            <span className="sr-only">Обновляем данные портфеля</span>
          </div>
        ) : (
          <div>
            <div className="page-heading">
              <div>
                <p className="eyebrow">Текущее состояние капитала</p>
                <h1>Портфель</h1>
              </div>
              <ConnectionLine state={state} data={query.data} />
            </div>

            <SyncStatusBanner
              state={state}
              sync={query.data?.sync}
              ageSeconds={query.data?.snapshot?.age_seconds}
              onRetry={() => void query.refetch()}
              retrying={query.isFetching}
            />

            <PortfolioBody state={state} data={query.data} />
          </div>
        )}
      </main>
    </div>
  );
}

/** Строка подключения в заголовке: точка статуса и краткое пояснение. */
function ConnectionLine({
  state,
  data,
}: {
  state: PortfolioViewState;
  data: PortfolioDto | undefined;
}) {
  if (data === undefined) {
    return (
      <div className="connection-line">
        <span className="status-dot neutral" />
        <span>Нет связи с сервером</span>
      </div>
    );
  }

  const account = data.broker.account;
  const connected = data.broker.status === 'connected';

  // В дизайне у точки два состояния: обычное и приглушённое. Приглушённая
  // означает «данные сейчас не подтверждены брокером».
  const healthy = connected && (state === 'fresh' || state === 'empty');

  return (
    <div className="connection-line">
      <span className={`status-dot${healthy ? '' : ' neutral'}`} />
      <span>
        {connected
          ? `Т-Банк подключён${account === null ? '' : ` · счёт ${account.masked_id}`}`
          : 'Доступ к Т-Банк не сконфигурирован'}
      </span>
    </div>
  );
}

function PortfolioBody({
  state,
  data,
}: {
  state: PortfolioViewState;
  data: PortfolioDto | undefined;
}) {
  if (state === 'no-broker') {
    return (
      <section className="state-panel">
        <div className="state-content">
          <div className="state-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 12h8M12 8v8M5 5l14 14" />
              <rect x="3" y="4" width="18" height="16" rx="3" />
            </svg>
          </div>
          <h2>Доступ к Т-Банк не сконфигурирован</h2>
          <p>
            Сервер Financial AI не получил действующий read-only доступ к T-Bank Invest API.
            Администратору системы нужно проверить токен в конфигурации сервера.
          </p>
        </div>
      </section>
    );
  }

  if (data === undefined || data.snapshot === null) {
    return (
      <section className="state-panel">
        <div className="state-content">
          <div className="state-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v5M12 16.5v.5" />
            </svg>
          </div>
          <h2>Данных о счёте пока нет</h2>
          <p>
            Ни одна синхронизация с брокером ещё не завершилась успешно. Нулевые суммы не
            показываются, чтобы их нельзя было принять за фактические.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section>
      <CapitalStrip snapshot={data.snapshot} sync={data.sync} />

      <PositionsSection positions={data.snapshot.positions} />
    </section>
  );
}
