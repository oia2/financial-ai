import type { SnapshotDto, SyncDto } from '@/entities/portfolio';
import { isNegative } from '@/shared/lib/decimal';
import {
  formatAge,
  formatDateTime,
  formatMoney,
  formatPercent,
  formatSignedMoney,
  formatSignedPercent,
  formatTime,
} from '@/shared/lib/format';

/**
 * Полоса капитала по утверждённому дизайну: слева крупная общая стоимость,
 * справа три факта — денежные средства, P&L и актуальность.
 *
 * Состав и порядок показателей заданы дизайном (FR-011, FR-017) и здесь не
 * меняются. Актуальность живёт именно тут, а не отдельным блоком.
 */
export function CapitalStrip({ snapshot, sync }: { snapshot: SnapshotDto; sync: SyncDto }) {
  const pnlNegative = isNegative(snapshot.unrealized_pnl);
  const pnlPercent = formatSignedPercent(snapshot.unrealized_pnl_percent);
  const failed = sync.status === 'failed';

  return (
    <div className="capital-strip">
      <div className="capital-primary">
        <span className="metric-label">Общая стоимость</span>
        <strong className="capital-value">{formatMoney(snapshot.total_value)}</strong>
        <span className="data-note">
          {snapshot.positions_count === 0
            ? 'Позиций нет, показаны денежные средства'
            : `Позиций: ${snapshot.positions_count}`}
        </span>
      </div>

      <div className="capital-facts">
        <div className="fact">
          <span className="metric-label">Денежные средства</span>
          <strong className="fact-value">{formatMoney(snapshot.cash)}</strong>
          <span className="fact-sub">{formatPercent(snapshot.cash_share, 1) ?? '0%'} портфеля</span>
        </div>

        <div className="fact">
          <span className="metric-label">Текущий P&amp;L</span>
          {/* Знак P&L различается визуально (FR-013). */}
          <strong className={`fact-value ${pnlNegative ? 'negative' : 'positive'}`}>
            {formatSignedMoney(snapshot.unrealized_pnl)}
          </strong>
          <span className="fact-sub">
            {/* Процент не определён при нулевой базе — ложный ноль недопустим. */}
            {pnlPercent === null ? 'Процент не определён' : `${pnlPercent} к средней стоимости`}
          </span>
        </div>

        <div className="fact">
          <span className="metric-label">Актуальность</span>
          {/*
            Возраст показывается во всех состояниях, где данные видны, включая
            сбой брокера: FR-014 и SC-006 требуют его именно там, где цифры
            на экране могут быть устаревшими.
          */}
          <strong className="fact-value">{formatAge(snapshot.age_seconds)}</strong>
          <span className="fact-sub">
            {sync.last_success_at === null
              ? 'Синхронизации ещё не было'
              : failed
                ? `Не обновляется · последняя успешная синхронизация ${formatTime(sync.last_success_at)}`
                : sync.is_stale
                  ? `Последняя успешная синхронизация ${formatTime(sync.last_success_at)}`
                  : `Обновлено ${formatDateTime(sync.last_success_at)}`}
          </span>
          <span className="fact-sub">Автообновление каждые {sync.refresh_interval_seconds} с</span>
        </div>
      </div>
    </div>
  );
}
