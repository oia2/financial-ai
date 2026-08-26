import {
  selectPortfolioState,
  usePortfolioQuery,
  type PortfolioDto,
  type PortfolioViewState,
} from '@/entities/portfolio';
import { Freshness } from '@/widgets/freshness/Freshness';
import { PortfolioSummary } from '@/widgets/portfolio-summary/PortfolioSummary';
import { PositionsTable } from '@/widgets/positions-table/PositionsTable';

import './portfolio-page.css';

/**
 * Раздел «Портфель».
 *
 * Все состояния (FR-015) выводятся из одного ответа API и факта его наличия;
 * ничего о состоянии брокера страница не вычисляет сама.
 */
export function PortfolioPage() {
  const query = usePortfolioQuery();
  const state = selectPortfolioState(query.data, query.error, query.isPending);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="product muted">FINANCIAL AI · Портфель</p>
          <h1 className="page-title">Портфель</h1>
        </div>

        {query.data ? (
          <div className="header-side">
            <AccountLine data={query.data} />
            <Freshness snapshot={query.data.snapshot} sync={query.data.sync} />
          </div>
        ) : null}
      </header>

      <main className="page-main">
        <PortfolioBody state={state} data={query.data} />
      </main>
    </div>
  );
}

function AccountLine({ data }: { data: PortfolioDto }) {
  if (data.broker.account === null) return null;

  return (
    <p className="account-line muted">
      {/* Номер договора — только маскированный (FR-022). */}
      Т-Банк · {data.broker.account.masked_id}
    </p>
  );
}

function PortfolioBody({
  state,
  data,
}: {
  state: PortfolioViewState;
  data: PortfolioDto | undefined;
}) {
  if (state === 'loading') {
    return <p className="state-panel">Обновляем данные портфеля…</p>;
  }

  if (state === 'no-broker') {
    return (
      <section className="state-panel">
        <h2>Доступ к Т-Банк не сконфигурирован</h2>
        <p className="muted">
          Сервер Financial AI не получил действующий read-only доступ к T-Bank Invest API.
          Администратору системы нужно проверить токен в конфигурации сервера.
        </p>
      </section>
    );
  }

  if (data === undefined || data.snapshot === null) {
    return (
      <section className="state-panel">
        <h2>Данных о счёте пока нет</h2>
        <p className="muted">
          Ни одна синхронизация с брокером ещё не завершилась успешно. Нулевые суммы не
          показываются, чтобы их нельзя было принять за фактические.
        </p>
      </section>
    );
  }

  return (
    <>
      <PortfolioSummary snapshot={data.snapshot} />
      <section className="positions-section">
        <h2 className="section-title">Позиции</h2>
        <PositionsTable positions={data.snapshot.positions} />
      </section>
    </>
  );
}
