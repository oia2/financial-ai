/**
 * Таблица плана: целевые позиции и разница к текущим.
 *
 * Границы, которые обязаны быть видны на экране (contracts/ui-states.md §3):
 *
 *  - **цена — закрытие названной сессии**, а не текущая котировка. Дата стоит
 *    рядом с ценой всегда, иначе одно принимается за другое;
 *  - **облигации и денежный фонд планом не затрагиваются.** Они стоят в той же
 *    таблице с пометкой «вне отбора»: отдельный список внизу читался бы как
 *    «про них забыли», а строка в общем составе говорит обратное;
 *  - **актив, выпавший из плана, назван с причиной**;
 *  - **расчёт портфель не меняет.** Действий исполнения в разделе нет
 *    (FR-063), и кнопки «купить» здесь не появится.
 *
 * Выгрузка плана в объём фичи не входит, поэтому кнопки выгрузки из прототипа
 * здесь нет: кнопка, которая ничего не делает, хуже её отсутствия.
 */

import { actionOf, type PlanAction, type PlanDto } from '@/entities/portfolio-plan';
import { formatMoney, formatNumber, formatPercent, formatQuantity } from '@/shared/lib/format';
import { DASH, formatIsoDate } from '@/shared/lib/market-format';
import { Pagination } from '@/shared/ui/Pagination';

const FILTERS: Array<{ value: PlanAction | 'all'; label: string }> = [
  { value: 'all', label: 'Все позиции' },
  { value: 'buy', label: 'К покупке' },
  { value: 'sell', label: 'К продаже' },
  { value: 'keep', label: 'Без изменения' },
];

/** Строка таблицы: план по активу или инструмент вне вселенной модели. */
interface Row {
  key: string;
  odId: string;
  ticker: string;
  note: string;
  action: PlanAction;
  current: string;
  target: string;
  weightAfter: string;
  price: string | null;
  deltaValue: string | null;
  deltaQuantity: string;
  lotSize: number | null;
}

function planRows(plan: PlanDto): Row[] {
  const positions: Row[] = plan.positions.map((row) => ({
    key: row.asset_id,
    odId: `plan-position-${row.ticker.toLowerCase()}`,
    ticker: row.ticker,
    note: `${row.name ?? row.asset_id} · № ${row.rank}`,
    action: actionOf(row),
    current: row.current_quantity,
    target: row.target_quantity,
    weightAfter: row.weight_after,
    price: row.price,
    deltaValue: row.delta_value,
    deltaQuantity: row.delta_quantity,
    lotSize: row.lot_size,
  }));

  const untouched: Row[] = plan.untouched.map((row) => ({
    key: `untouched-${row.ticker ?? row.name ?? ''}`,
    odId: `plan-position-${(row.ticker ?? 'без-тикера').toLowerCase()}`,
    ticker: row.ticker ?? DASH,
    note: `${row.name ?? ''} · вне отбора`,
    // Инструмент вне вселенной модели остаётся как есть: это и есть план по
    // нему, а не отсутствие плана.
    action: 'keep',
    current: row.quantity,
    target: row.quantity,
    weightAfter: row.weight_after,
    // Цены закрытия у него нет: рыночные данные его не ведут, а брокерскую
    // цену подставлять в колонку «из снимка» значило бы смешать источники.
    price: null,
    deltaValue: null,
    deltaQuantity: '0',
    lotSize: null,
  }));

  return [...positions, ...untouched];
}

function tradeText(action: PlanAction, delta: string): string {
  if (action === 'keep') return 'Без изменения';
  const quantity = formatQuantity(delta.replace('-', ''));
  return `${action === 'buy' ? 'Купить' : 'Продать'} ${quantity} шт.`;
}

export function PositionPlan({
  plan,
  filter,
  page,
  pageSize,
  onFilterChange,
  onPageChange,
  onPageSizeChange,
  onOpenRun,
}: {
  plan: PlanDto;
  filter: PlanAction | 'all';
  page: number;
  pageSize: number;
  onFilterChange: (filter: PlanAction | 'all') => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  onOpenRun: () => void;
}) {
  const rows = planRows(plan).filter((row) => filter === 'all' || row.action === filter);
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const visible = rows.slice((page - 1) * pageSize, page * pageSize);

  return (
    <>
      <div className="pp-source" data-od-id="position-plan-source">
        <span>
          Ранжирование:{' '}
          <button
            className="pp-source-link"
            type="button"
            data-od-id="position-plan-open-source"
            onClick={onOpenRun}
          >
            {formatIsoDate(plan.asof_date)} ↗
          </button>
        </span>
        <span>
          Цены: закрытие сессии{' '}
          <span className="mono">{formatIsoDate(plan.price_source.session_date)}</span>
        </span>
        <span>
          Расчётный капитал: <span className="mono">{formatMoney(plan.capital.eligible)}</span>
        </span>
      </div>

      {plan.emulated && (
        <div className="pp-disclosure" data-od-id="plan-on-emulated-ranking">
          <strong>План построен на вымышленных скорах</strong>
          <span>
            Состав задан ранжированием эмулятора: модели за ним нет. Расчёт счёт не меняет и заявок
            не отправляет.
          </span>
        </div>
      )}

      <details className="pp-strategy" data-od-id="position-plan-strategy">
        <summary data-od-id="open-position-plan-strategy">
          <span>Правило</span>
          <strong>{plan.policy_title}</strong>
          <span className="pp-strategy-hint">Как посчитано</span>
        </summary>
        <div className="pp-strategy-body">
          <p>
            Ранжирование определяет состав, правило распределения — веса. Текущие позиции и деньги
            счёта используются для расчёта изменений.
          </p>
          <dl className="pp-strategy-parameters">
            <div>
              <dt>Лимит распределения</dt>
              <dd className="mono">{formatMoney(plan.capital.limit)}</dd>
            </div>
            <div>
              <dt>Распределено</dt>
              <dd className="mono">{formatMoney(plan.capital.allocated)}</dd>
            </div>
            <div>
              <dt>Останется деньгами</dt>
              <dd className="mono">{formatMoney(plan.capital.cash_after)}</dd>
            </div>
            <div>
              <dt>Комиссия за обе стороны</dt>
              <dd className="mono">{formatMoney(plan.capital.fee_total)}</dd>
            </div>
          </dl>
          <ol>
            <li>Целевая сумма актива — расчётный капитал, умноженный на вес правила.</li>
            <li>
              Количество округляется вниз до полного лота. Остаток округления остаётся деньгами и
              другим активам не достаётся.
            </li>
            <li>
              Вес актива, для которого неизвестны цена или лот, уходит в деньги, а не
              перераспределяется: перераспределение исказило бы доли относительно правила.
            </li>
            <li>Облигации и денежный фонд сохраняются и в расчётный капитал не входят.</li>
          </ol>
          <p data-od-id="strategy-snapshot-price">
            «Цена из снимка» — закрытие торговой сессии{' '}
            <span className="mono">{formatIsoDate(plan.price_source.session_date)}</span>, а не
            текущая котировка. По ней рассчитаны количества и суммы; цена сделки может отличаться.
          </p>
          <p data-od-id="strategy-action-value">
            «Оценка изменения» — количество к покупке или продаже, умноженное на цену из снимка, без
            комиссии. Знак «−» означает расход денег на покупку, «+» — поступление от продажи. Это
            сумма только покупаемых или продаваемых акций, а не стоимость всей позиции.
          </p>
          <p>
            «После плана» означает после выполнения рассчитанных изменений. Сам расчёт счёт не
            меняет.
          </p>
        </div>
      </details>

      <div className="pp-table-top">
        <h2>Действия по портфелю</h2>
        <label htmlFor="ppFilter">
          Показать
          <select
            id="ppFilter"
            data-od-id="position-plan-filter"
            value={filter}
            onChange={(event) => onFilterChange(event.target.value as PlanAction | 'all')}
          >
            {FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="pp-table-help" id="ppTableHelp" data-od-id="plan-table-explanation">
        Доля после плана — от всего счёта, включая деньги, облигации и фонд. Цена из снимка
        используется для оценки суммы; цена заявки не задаётся, и заявки не отправляются.
      </p>

      {visible.length === 0 ? (
        <div className="pp-empty-filter">
          Позиций с таким изменением нет. Выберите другой фильтр.
        </div>
      ) : (
        <div className="pp-table-wrap">
          <table
            className="pp-table"
            data-od-id="position-plan-table"
            aria-describedby="ppTableHelp"
          >
            <colgroup>
              <col style={{ width: '23%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '20%' }} />
              <col style={{ width: '13%' }} />
              <col style={{ width: '14%' }} />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Актив</th>
                <th scope="col">Сейчас, шт.</th>
                <th scope="col">После, шт.</th>
                <th scope="col">Доля после плана</th>
                <th scope="col">Изменение</th>
                <th scope="col">Цена из снимка, ₽</th>
                <th scope="col">Оценка изменения, ₽</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={row.key} data-od-id={row.odId}>
                  <td>
                    <strong className="pp-ticker">{row.ticker}</strong>
                    <span className="pp-cell-note">{row.note}</span>
                  </td>
                  <td data-label="Сейчас, шт." className="mono">
                    {formatQuantity(row.current)}
                  </td>
                  <td data-label="После, шт." className="mono">
                    {formatQuantity(row.target)}
                  </td>
                  <td data-label="Доля после плана" className="mono">
                    {formatPercent(row.weightAfter)}
                  </td>
                  <td data-label="Изменение">
                    <span className={`pp-trade ${row.action}`}>
                      {tradeText(row.action, row.deltaQuantity)}
                    </span>
                    {row.lotSize !== null && (
                      <span className="pp-cell-note">
                        лот <span className="mono">{row.lotSize}</span> шт.
                      </span>
                    )}
                  </td>
                  <td data-label="Цена из снимка, ₽" className="mono">
                    {row.price === null ? DASH : formatNumber(row.price)}
                  </td>
                  <td data-label="Оценка изменения, ₽" className="mono">
                    {row.deltaValue === null ? DASH : formatNumber(row.deltaValue)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={page}
        pageCount={pageCount}
        pageSize={pageSize}
        label="Навигация по действиям"
        odId="position-plan-pagination"
        pageSizeOdId="position-plan-page-size"
        prevOdId="position-plan-prev"
        nextOdId="position-plan-next"
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />

      {plan.excluded.length > 0 && (
        <div className="ml-retention-note" data-od-id="position-plan-excluded">
          <strong>В план не вошли</strong>
          <p>
            {plan.excluded
              .map((row) => `${row.asset_id} (№ ${row.rank}): ${row.reason}`)
              .join('; ')}
            . Вес такого актива остался деньгами и между остальными не перераспределён: иначе доли
            разошлись бы с правилом.
          </p>
        </div>
      )}
    </>
  );
}
