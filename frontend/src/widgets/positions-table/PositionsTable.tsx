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
import { isNegative, parseDecimal } from '@/shared/lib/decimal';
import { formatPercent, formatPrice, formatQuantity, formatSignedMoney } from '@/shared/lib/format';

import './positions-table.css';

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

const columns = [
  columnHelper.accessor((row) => row.ticker ?? row.instrument_uid, {
    id: 'instrument',
    header: 'Инструмент',
    cell: (cell) => {
      const position = cell.row.original;
      return (
        <span className="instrument">
          {/* Отсутствие тикера и названия не блокирует отображение позиции. */}
          <span className="instrument-ticker">{position.ticker ?? position.instrument_uid}</span>
          {position.name ? <span className="instrument-name">{position.name}</span> : null}
        </span>
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
    cell: (cell) => formatPrice(cell.getValue()),
    sortingFn: decimalSort,
  }),
  columnHelper.accessor('unrealized_pnl', {
    header: 'P&L',
    cell: (cell) => {
      const value = cell.getValue();
      if (value === null) return '—';
      const percent = formatPercent(cell.row.original.unrealized_pnl_percent);
      return (
        <span className={isNegative(value) ? 'negative' : 'positive'}>
          {formatSignedMoney(value)}
          {percent === null ? null : <span className="muted"> ({percent})</span>}
        </span>
      );
    },
    sortingFn: decimalSort,
  }),
  columnHelper.accessor('share', {
    header: 'Доля',
    cell: (cell) => formatPercent(cell.getValue(), 1),
    sortingFn: decimalSort,
  }),
];

export function PositionsTable({ positions }: { positions: PositionDto[] }) {
  // По умолчанию — убывание доли в портфеле (FR-018).
  const [sorting, setSorting] = useState<SortingState>([{ id: 'share', desc: true }]);

  const table = useReactTable({
    data: positions,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    // Идентичность строки — инструмент, а не порядковый номер: при фоновом
    // обновлении React переиспользует те же узлы, поэтому прокрутка и
    // выбранная сортировка не сбрасываются (US2 AS2).
    getRowId: (row) => row.instrument_uid,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (positions.length === 0) {
    return (
      <div className="table-frame">
        <p className="empty-state">В портфеле пока нет позиций</p>
      </div>
    );
  }

  return (
    <div className="table-frame">
      <div className="table-scroll">
        <table className="positions-table numeric" aria-label="Позиции">
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  return (
                    <th key={header.id} aria-sort={ariaSort(sorted)}>
                      <button
                        type="button"
                        className="sort-button"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span className="sort-indicator" aria-hidden="true">
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
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ariaSort(sorted: false | 'asc' | 'desc'): 'ascending' | 'descending' | 'none' {
  if (sorted === 'asc') return 'ascending';
  if (sorted === 'desc') return 'descending';
  return 'none';
}
