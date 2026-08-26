import type { SnapshotDto } from '@/entities/portfolio';
import { isNegative } from '@/shared/lib/decimal';
import {
  formatMoney,
  formatPercent,
  formatSignedMoney,
  formatSignedPercent,
} from '@/shared/lib/format';

import './portfolio-summary.css';

/**
 * Сводные показатели капитала (FR-011): общая стоимость, денежные средства
 * с долей, нереализованный P&L в рублях и процентах, количество позиций.
 */
export function PortfolioSummary({ snapshot }: { snapshot: SnapshotDto }) {
  const pnlNegative = isNegative(snapshot.unrealized_pnl);
  const pnlPercent = formatSignedPercent(snapshot.unrealized_pnl_percent);

  return (
    <section className="summary" aria-label="Текущее состояние капитала">
      <Metric label="Общая стоимость" value={formatMoney(snapshot.total_value)} primary />

      <Metric
        label="Денежные средства"
        value={formatMoney(snapshot.cash)}
        note={`${formatPercent(snapshot.cash_share) ?? '0%'} портфеля`}
      />

      <Metric
        label="Текущий P&L"
        value={formatSignedMoney(snapshot.unrealized_pnl)}
        // Знак P&L различается визуально (FR-013).
        tone={pnlNegative ? 'negative' : 'positive'}
        // Процент отсутствует, когда база стоимости нулевая — ложный ноль
        // здесь недопустим.
        note={pnlPercent === null ? 'Процент не определён' : `${pnlPercent} к средней стоимости`}
      />

      <Metric label="Позиции" value={String(snapshot.positions_count)} />
    </section>
  );
}

function Metric({
  label,
  value,
  note,
  tone,
  primary = false,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: 'positive' | 'negative';
  primary?: boolean;
}) {
  return (
    <div className={`metric${primary ? ' metric-primary' : ''}`}>
      <span className="metric-label muted">{label}</span>
      <span className={`metric-value numeric${tone ? ` ${tone}` : ''}`}>{value}</span>
      {note ? <span className="metric-note muted">{note}</span> : null}
    </div>
  );
}
