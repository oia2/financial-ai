import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from '@tanstack/react-table';
import { useState } from 'react';

import type { PositionDto } from '@/entities/portfolio';
import { isNegative, isZero, parseDecimal, shiftDecimal } from '@/shared/lib/decimal';
import { formatPercent, formatPrice, formatQuantity, formatSignedMoney } from '@/shared/lib/format';

const columnHelper = createColumnHelper<PositionDto>();

/**
 * Сравнение денежных значений без превращения в number: строковые Decimal
 * сравниваются по знаку, длине целой части и лексикографически.
 */
function compareDecimalStrings(left: string | null, right: string | null): number {
  if (left === null && right === null) return 0;
  // Позиции с неизвестным значением уходят в конец при любом направлении.
  if (left === null) return 1;
  if (right === null) return -1;

  const a = parseDecimal(left);
  const b = parseDecimal(right);

  if (a.negative !== b.negative) return a.negative ? -1 : 1;

  const sign = a.negative ? -1 : 1;
  if (a.int.length !== b.int.length) return a.int.length > b.int.length ? sign : -sign;

  const aDigits = `${a.int}.${a.frac}`;
  const bDigits = `${b.int}.${b.frac}`;
  if (aDigits === bDigits) return 0;
  return aDigits > bDigits ? sign : -sign;
}

const decimalSort = (
  rowA: { original: PositionDto },
  rowB: { original: PositionDto },
  id: string,
) =>
  compareDecimalStrings(
    (rowA.original as unknown as Record<string, string | null>)[id] ?? null,
    (rowB.original as unknown as Record<string, string | null>)[id] ?? null,
  );

/** Ширина полосы доли в процентах — как в дизайне. */
function weightWidth(share: string): number {
  const percent = Number(shiftDecimal(share, 2));
  return Math.max(0, Math.min(100, percent));
}

const columns = [
  columnHelper.accessor((row) => row.ticker ?? row.instrument_uid, {
    id: 'instrument',
    header: 'Инструмент',
    cell: (cell) => {
      const position = cell.row.original;
      return (
        <>
          {/* Отсутствие тикера и названия не блокирует отображение позиции. */}
          <span className="ticker">{position.ticker ?? position.instrument_uid}</span>
          <span className="instrument-name">{position.name ?? '—'}</span>
        </>
      );
    },
    sortingFn: 'alphanumeric',
  }),
  columnHelper.accessor('quantity', {
    header: 'Количество',
    cell: (cell) => formatQuantity(cell.getValue()),
    sortingFn: decimalSort,
  }),
  columnHelper.accessor('average_price', {
    header: 'Средняя цена',
    cell: (cell) => formatPrice(cell.getValue()) ?? '—',
    sortingFn: decimalSort,
  }),
  columnHelper.accessor('current_price', {
    header: 'Текущая цена',
    cell: (cell) => formatPrice(cell.getValue()),
    sortingFn: decimalSort,
  }),
  columnHelper.accessor('value', {
    header: 'Стоимость',
    cell: (cell) => (
      <>
        {formatPrice(cell.getValue())}
        {/* У облигаций стоимость больше «количество × цена» на накопленный
            купонный доход — так же считает брокер. */}
        {isZero(cell.row.original.accrued_interest) ? null : (
          <span className="instrument-name"> вкл. НКД</span>
        )}
      </>
    ),
    sortingFn: decimalSort,
  }),
  columnHelper.accessor('unrealized_pnl', {
    header: 'P&L',
    cell: (cell) => {
      const value = cell.getValue();
      return value === null ? '—' : formatSignedMoney(value);
    },
    sortingFn: decimalSort,
  }),
  columnHelper.accessor('share', {
    header: 'Доля',
    cell: (cell) => {
      const value = cell.getValue();
      return (
        <div className="weight-cell">
          <span className="weight-value">{formatPercent(value, 1)}</span>
          <span className="weight-track">
            <span className="weight-fill" style={{ width: `${weightWidth(value)}%` }} />
          </span>
        </div>
      );
    },
    sortingFn: decimalSort,
  }),
];

/** Подписи для мобильной раскладки — дизайн выводит их через data-label. */
const MOBILE_LABELS: Record<string, string> = {
  quantity: 'Количество',
  average_price: 'Средняя цена',
  current_price: 'Текущая цена',
  value: 'Стоимость',
  unrealized_pnl: 'P&L',
  share: 'Доля',
};

export function PositionsTable({ positions }: { positions: PositionDto[] }) {
  // По умолчанию — убывание доли в портфеле (FR-018).
  const [sorting, setSorting] = useState<SortingState>([{ id: 'share', desc: true }]);

  const table = useReactTable({
    data: positions,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    // Повторный выбор столбца меняет направление, а не снимает сортировку.
    enableSortingRemoval: false,
    // Идентичность строки — инструмент, а не порядковый номер: при фоновом
    // обновлении React переиспользует те же узлы, поэтому прокрутка и
    // выбранная сортировка не сбрасываются (US2 AS2).
    getRowId: (row) => row.instrument_uid,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (positions.length === 0) {
    return (
      <div className="state-panel">
        <div className="state-content">
          <div className="state-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 7h16M6 7l1 12h10l1-12M9 7V4h6v3" />
            </svg>
          </div>
          <h2>В портфеле пока нет позиций</h2>
          <p>
            Счёт подключён и синхронизирован. Позиции появятся здесь после зачисления активов на
            брокерский счёт.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="table-frame">
      <table aria-label="Позиции">
        <colgroup>
          <col className="instrument" />
          <col className="quantity" />
          <col className="price" />
          <col className="price" />
          <col className="value" />
          <col className="pnl" />
          <col className="weight" />
        </colgroup>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                return (
                  <th key={header.id} scope="col" aria-sort={ariaSort(sorted)}>
                    <button
                      type="button"
                      className={`sort-button${sorted ? ' active' : ''}`}
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}{' '}
                      <span className="sort-arrow" aria-hidden="true">
                        {sorted === 'asc' ? '↑' : sorted === 'desc' ? '↓' : ''}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => {
                const id = cell.column.id;
                const pnl = id === 'unrealized_pnl' ? cell.getValue<string | null>() : null;
                const className =
                  id === 'instrument'
                    ? 'instrument-cell'
                    : pnl === null
                      ? undefined
                      : isNegative(pnl)
                        ? 'pnl-negative'
                        : 'pnl-positive';

                return (
                  <td key={cell.id} className={className} data-label={MOBILE_LABELS[id]}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ariaSort(sorted: false | 'asc' | 'desc'): 'ascending' | 'descending' | 'none' {
  if (sorted === 'asc') return 'ascending';
  if (sorted === 'desc') return 'descending';
  return 'none';
}
