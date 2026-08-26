/**
 * Сортировка таблицы позиций (T078, FR-018).
 *
 * Сравнение идёт по строковым Decimal, без превращения в number: иначе
 * порядок мог бы разойтись с отображаемыми значениями на больших суммах.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { PositionDto } from '@/entities/portfolio';
import { PositionsTable } from '@/widgets/positions-table/PositionsTable';

function position(overrides: Partial<PositionDto> & { instrument_uid: string }): PositionDto {
  return {
    ticker: overrides.instrument_uid.toUpperCase(),
    name: null,
    asset_type: 'share',
    currency: 'RUB',
    quantity: '10',
    average_price: '100',
    current_price: '110',
    accrued_interest: '0',
    value: '1100',
    unrealized_pnl: '100',
    unrealized_pnl_percent: '0.1',
    share: '0.5',
    ...overrides,
  };
}

const positions: PositionDto[] = [
  position({ instrument_uid: 'aaa', value: '100', share: '0.1', unrealized_pnl: '-50' }),
  position({
    instrument_uid: 'bbb',
    value: '9000000000000000',
    share: '0.7',
    unrealized_pnl: '10',
  }),
  position({ instrument_uid: 'ccc', value: '250', share: '0.2', unrealized_pnl: null }),
];

function rowOrder(): string[] {
  const table = screen.getByRole('table', { name: 'Позиции' });
  const body = within(table).getAllByRole('rowgroup')[1];
  return within(body!)
    .getAllByRole('row')
    .map((row) => row.querySelector('.ticker')?.textContent ?? '');
}

async function clickHeader(name: RegExp) {
  await userEvent.click(screen.getByRole('button', { name }));
}

describe('сортировка позиций', () => {
  it('по умолчанию сортирует по убыванию доли', () => {
    render(<PositionsTable positions={positions} />);

    expect(rowOrder()).toEqual(['BBB', 'CCC', 'AAA']);
  });

  it('показывает направление сортировки', () => {
    render(<PositionsTable positions={positions} />);

    const shareHeader = screen.getByRole('columnheader', { name: /Доля/ });
    expect(shareHeader).toHaveAttribute('aria-sort', 'descending');
  });

  it('переключает направление при повторном выборе столбца', async () => {
    render(<PositionsTable positions={positions} />);

    await clickHeader(/Доля/);

    expect(rowOrder()).toEqual(['AAA', 'CCC', 'BBB']);
    expect(screen.getByRole('columnheader', { name: /Доля/ })).toHaveAttribute(
      'aria-sort',
      'ascending',
    );
  });

  it('сортирует по стоимости без потери точности на больших значениях', async () => {
    render(<PositionsTable positions={positions} />);

    await clickHeader(/Стоимость/);

    // Значение за пределами точности double не должно ломать порядок.
    expect(rowOrder()).toEqual(['AAA', 'CCC', 'BBB']);
  });

  it('сортирует по инструменту', async () => {
    render(<PositionsTable positions={positions} />);

    await clickHeader(/Инструмент/);

    expect(rowOrder()).toEqual(['AAA', 'BBB', 'CCC']);
  });

  it('учитывает знак при сортировке по P&L', async () => {
    render(<PositionsTable positions={positions} />);

    await clickHeader(/P&L/);

    // Отрицательный P&L идёт первым, позиция без известного P&L — последней.
    expect(rowOrder()).toEqual(['AAA', 'BBB', 'CCC']);
  });

  it('сортирует по количеству и ценам', async () => {
    const varied = [
      position({ instrument_uid: 'aaa', quantity: '5', current_price: '300' }),
      position({ instrument_uid: 'bbb', quantity: '100', current_price: '10' }),
    ];

    render(<PositionsTable positions={varied} />);

    await clickHeader(/Количество/);
    expect(rowOrder()).toEqual(['AAA', 'BBB']);

    await clickHeader(/Текущая цена/);
    expect(rowOrder()).toEqual(['BBB', 'AAA']);
  });

  it('показывает пустое состояние без таблицы', () => {
    render(<PositionsTable positions={[]} />);

    expect(screen.getByText('В портфеле пока нет позиций')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

describe('облигации с НКД', () => {
  it('поясняет, что стоимость включает накопленный купонный доход', () => {
    render(
      <PositionsTable
        positions={[
          position({
            instrument_uid: 'ofz',
            accrued_interest: '19.674',
            current_price: '1029.06',
            quantity: '700',
            value: '734113.80',
          }),
        ]}
      />,
    );

    expect(screen.getByText(/вкл\. НКД/)).toBeInTheDocument();
  });

  it('не показывает пояснение у обычных позиций', () => {
    render(<PositionsTable positions={[position({ instrument_uid: 'sber' })]} />);

    expect(screen.queryByText(/вкл\. НКД/)).not.toBeInTheDocument();
  });
});
