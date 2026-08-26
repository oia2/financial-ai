/**
 * Панель инструментов таблицы: бейдж количества, подсветка P&L и пагинация.
 *
 * Поведение задано обновлённым дизайном Open Design: один переключатель
 * подсветки в шапке таблицы, выбор 10/25/50 строк, компактная пагинация.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import type { PositionDto } from '@/entities/portfolio';
import { formatPositionCount } from '@/shared/lib/plural';
import { PositionsSection } from '@/widgets/positions-section/PositionsSection';

function position(uid: string, pnl: string | null = '100'): PositionDto {
  return {
    instrument_uid: uid,
    ticker: uid.toUpperCase(),
    name: null,
    asset_type: 'share',
    currency: 'RUB',
    quantity: '10',
    average_price: '100',
    current_price: '110',
    accrued_interest: '0',
    value: '1100',
    unrealized_pnl: pnl,
    unrealized_pnl_percent: pnl === null ? null : '0.1',
    share: '0.5',
  };
}

function manyPositions(count: number): PositionDto[] {
  return Array.from({ length: count }, (_, index) =>
    position(`p${String(index).padStart(2, '0')}`, index % 2 === 0 ? '100' : '-100'),
  );
}

function rows() {
  const body = within(screen.getByRole('table', { name: 'Позиции' })).getAllByRole('rowgroup')[1];
  return within(body!).getAllByRole('row');
}

beforeEach(() => localStorage.clear());

describe('бейдж количества позиций', () => {
  it('склоняет слово по числу', () => {
    expect(formatPositionCount(1)).toBe('1 позиция');
    expect(formatPositionCount(2)).toBe('2 позиции');
    expect(formatPositionCount(5)).toBe('5 позиций');
    expect(formatPositionCount(11)).toBe('11 позиций');
    expect(formatPositionCount(21)).toBe('21 позиция');
    expect(formatPositionCount(0)).toBe('0 позиций');
  });

  it('показывает бейдж рядом с заголовком', () => {
    render(<PositionsSection positions={manyPositions(3)} />);

    expect(screen.getByText('3 позиции')).toBeInTheDocument();
  });

  it('не дублирует строку последней синхронизации над таблицей', () => {
    render(<PositionsSection positions={manyPositions(3)} />);

    expect(screen.queryByText(/Последняя синхронизация/)).not.toBeInTheDocument();
  });
});

describe('подсветка P&L', () => {
  it('по умолчанию выключена', () => {
    render(<PositionsSection positions={manyPositions(2)} />);

    expect(screen.getByRole('switch')).not.toBeChecked();
    expect(screen.getByText('Выключена')).toBeInTheDocument();
    expect(rows().some((row) => row.className.includes('pnl-highlight'))).toBe(false);
  });

  it('окрашивает строки по знаку P&L при включении', async () => {
    render(<PositionsSection positions={manyPositions(2)} />);

    await userEvent.click(screen.getByRole('switch'));

    expect(screen.getByText('Включена')).toBeInTheDocument();
    const classes = rows().map((row) => row.className);
    expect(classes).toContain('pnl-highlight-positive');
    expect(classes).toContain('pnl-highlight-negative');
  });

  it('не подсвечивает позиции с неизвестным P&L', async () => {
    render(<PositionsSection positions={[position('a', null)]} />);

    await userEvent.click(screen.getByRole('switch'));

    expect(rows()[0]!.className).toBe('');
  });

  it('сохраняет состояние переключателя между открытиями', async () => {
    const { unmount } = render(<PositionsSection positions={manyPositions(2)} />);
    await userEvent.click(screen.getByRole('switch'));
    unmount();

    render(<PositionsSection positions={manyPositions(2)} />);

    expect(screen.getByRole('switch')).toBeChecked();
  });

  it('переживает смену сортировки', async () => {
    render(<PositionsSection positions={manyPositions(2)} />);
    await userEvent.click(screen.getByRole('switch'));

    await userEvent.click(screen.getByRole('button', { name: /Стоимость/ }));

    expect(rows().some((row) => row.className.includes('pnl-highlight'))).toBe(true);
  });
});

describe('пагинация', () => {
  it('по умолчанию показывает десять строк', () => {
    render(<PositionsSection positions={manyPositions(12)} />);

    expect(rows()).toHaveLength(10);
    expect(screen.getByLabelText('Количество строк на странице')).toHaveValue('10');
  });

  it('показывает компактный номер страницы без дублирования', () => {
    render(<PositionsSection positions={manyPositions(12)} />);

    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(screen.queryByText(/из 12/)).not.toBeInTheDocument();
  });

  it('листает страницы стрелками', async () => {
    render(<PositionsSection positions={manyPositions(12)} />);

    expect(screen.getByLabelText('Предыдущая страница')).toBeDisabled();
    await userEvent.click(screen.getByLabelText('Следующая страница'));

    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    expect(rows()).toHaveLength(2);
    expect(screen.getByLabelText('Следующая страница')).toBeDisabled();
  });

  it('меняет размер страницы и запоминает выбор', async () => {
    const { unmount } = render(<PositionsSection positions={manyPositions(30)} />);

    await userEvent.selectOptions(screen.getByLabelText('Количество строк на странице'), '25');
    expect(rows()).toHaveLength(25);

    unmount();
    render(<PositionsSection positions={manyPositions(30)} />);

    expect(screen.getByLabelText('Количество строк на странице')).toHaveValue('25');
    expect(rows()).toHaveLength(25);
  });

  it('возвращает на первую страницу при смене размера', async () => {
    render(<PositionsSection positions={manyPositions(30)} />);

    await userEvent.click(screen.getByLabelText('Следующая страница'));
    expect(screen.getByText('2 / 3')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('Количество строк на странице'), '50');

    expect(screen.getByText('1 / 1')).toBeInTheDocument();
  });

  it('не показывает таблицу и переключатель на пустом портфеле', () => {
    render(<PositionsSection positions={[]} />);

    expect(screen.getByText('В портфеле пока нет позиций')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.getByText('0 позиций')).toBeInTheDocument();
  });
});

describe('денежные значения в таблице', () => {
  it('показывает знак рубля в ценах и стоимости', () => {
    render(<PositionsSection positions={[position('sber')]} />);

    const cells = screen.getAllByRole('cell').map((cell) => cell.textContent ?? '');

    // В дизайне денежные ячейки содержат «1 002 ₽», а не голое число.
    expect(cells.filter((text) => text.includes('₽')).length).toBe(4);
  });

  it('не добавляет знак рубля к количеству и доле', () => {
    render(<PositionsSection positions={[position('sber')]} />);

    const cells = screen.getAllByRole('cell');
    expect(cells[1]?.textContent).not.toContain('₽');
    expect(cells[6]?.textContent).not.toContain('₽');
  });
});
