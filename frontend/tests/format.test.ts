/**
 * Форматирование под русскую локаль и строковая арифметика (T026).
 *
 * Точность из API не должна теряться по дороге к экрану: разбор идёт по
 * строке, а не через number (SC-002, FR-016).
 */

import { describe, expect, it } from 'vitest';

import { isNegative, isZero, roundDecimal, shiftDecimal } from '@/shared/lib/decimal';
import {
  formatAge,
  formatMoney,
  formatNumber,
  formatPercent,
  formatQuantity,
  formatSignedMoney,
  formatSignedPercent,
} from '@/shared/lib/format';

const NBSP = ' ';

describe('разделители разрядов', () => {
  it('группирует тысячи неразрывным пробелом', () => {
    expect(formatNumber('362064')).toBe(`362${NBSP}064`);
    expect(formatNumber('1234567.89')).toBe(`1${NBSP}234${NBSP}567,89`);
  });

  it('не группирует числа до тысячи', () => {
    expect(formatNumber('999.5')).toBe('999,5');
  });

  it('добавляет валюту к денежным значениям', () => {
    expect(formatMoney('40545')).toBe(`40${NBSP}545${NBSP}₽`);
  });
});

describe('округление до копеек', () => {
  it('округляет вверх от половины', () => {
    expect(formatNumber('0.005')).toBe('0,01');
    expect(formatNumber('1.235')).toBe('1,24');
  });

  it('округляет вниз ниже половины', () => {
    expect(formatNumber('1.2349')).toBe('1,23');
  });

  it('переносит разряд через девятки', () => {
    expect(formatNumber('9.999')).toBe('10');
    expect(formatNumber('999.999')).toBe(`1${NBSP}000`);
  });

  it('округляет по модулю для отрицательных', () => {
    expect(formatNumber('-1.235')).toBe('−1,24');
  });

  it('не теряет разряды на значениях за пределами точности double', () => {
    // Number('362064.123456789') уже даёт другое значение.
    expect(formatNumber('362064.129', 3)).toBe(`362${NBSP}064,129`);
    expect(formatNumber('9007199254740993.01')).toBe(
      `9${NBSP}007${NBSP}199${NBSP}254${NBSP}740${NBSP}993,01`,
    );
  });
});

describe('знак P&L', () => {
  it('добавляет плюс к положительным', () => {
    expect(formatSignedMoney('4590')).toBe(`+4${NBSP}590${NBSP}₽`);
  });

  it('сохраняет минус у отрицательных', () => {
    expect(formatSignedMoney('-4590')).toBe(`−4${NBSP}590${NBSP}₽`);
  });

  it('не добавляет знак к нулю', () => {
    expect(formatSignedMoney('0')).toBe(`0${NBSP}₽`);
  });
});

describe('проценты', () => {
  it('переводит долю единицы в проценты', () => {
    expect(formatPercent('0.0129')).toBe('1,29%');
    expect(formatPercent('0.899287', 1)).toBe('89,9%');
    expect(formatPercent('1')).toBe('100%');
  });

  it('сохраняет знак', () => {
    expect(formatSignedPercent('0.0722')).toBe('+7,22%');
    expect(formatSignedPercent('-0.0722')).toBe('−7,22%');
  });

  it('оставляет null, когда процент не определён', () => {
    // Ложный ноль недопустим: база стоимости неизвестна.
    expect(formatPercent(null)).toBeNull();
    expect(formatSignedPercent(null)).toBeNull();
  });
});

describe('количество', () => {
  it('убирает незначащие нули', () => {
    expect(formatQuantity('1200.000000000')).toBe(`1${NBSP}200`);
  });

  it('сохраняет дробное количество', () => {
    expect(formatQuantity('0.125')).toBe('0,125');
  });
});

describe('сдвиг запятой', () => {
  it('сдвигает вправо', () => {
    expect(shiftDecimal('0.0129', 2)).toBe('1.29');
    expect(shiftDecimal('1', 2)).toBe('100');
    expect(shiftDecimal('0.000000001', 2)).toBe('0.0000001');
  });

  it('сохраняет знак', () => {
    expect(shiftDecimal('-0.05', 2)).toBe('-5');
  });
});

describe('знак и ноль', () => {
  it('распознаёт отрицательные значения', () => {
    expect(isNegative('-0.01')).toBe(true);
    expect(isNegative('0')).toBe(false);
    expect(isNegative('-0')).toBe(false);
  });

  it('распознаёт ноль в любой записи', () => {
    expect(isZero('0')).toBe(true);
    expect(isZero('0.000000000')).toBe(true);
    expect(isZero('0.000000001')).toBe(false);
  });
});

describe('округление возвращает части числа', () => {
  it('дополняет дробную часть нулями', () => {
    expect(roundDecimal('5', 2)).toEqual({ negative: false, int: '5', frac: '00' });
  });
});

describe('возраст данных', () => {
  it('показывает «Сейчас» на свежих данных', () => {
    expect(formatAge(0)).toBe('Сейчас');
    expect(formatAge(59)).toBe('Сейчас');
  });

  it('переводит в минуты, часы и дни', () => {
    expect(formatAge(60)).toBe(`1${NBSP}мин назад`);
    expect(formatAge(28 * 60)).toBe(`28${NBSP}мин назад`);
    expect(formatAge(2 * 3600)).toBe(`2${NBSP}ч назад`);
    expect(formatAge(3 * 86400)).toBe(`3${NBSP}дн назад`);
  });
});
