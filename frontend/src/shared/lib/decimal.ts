/**
 * Строковая десятичная арифметика.
 *
 * API отдаёт денежные значения строками, чтобы не терять точность в double
 * (SC-002). Здесь они округляются и сдвигаются **не превращаясь в number**:
 * `Number('362064.123456789')` уже теряет разряды, а на суммах длинного
 * портфеля расхождение становится видимым.
 *
 * Нужны ровно три операции: разбор, сдвиг запятой (для процентов) и
 * округление до заданного знака. Полноценная библиотека произвольной
 * точности для этого избыточна.
 */

export interface DecimalParts {
  negative: boolean;
  int: string;
  frac: string;
}

const DECIMAL_RE = /^([+-]?)(\d*)(?:\.(\d*))?$/;

export function parseDecimal(value: string): DecimalParts {
  const match = DECIMAL_RE.exec(value.trim());
  if (!match) {
    throw new Error(`Некорректное десятичное значение: ${value}`);
  }

  const [, sign, intPart = '', fracPart = ''] = match;
  const int = (intPart ?? '').replace(/^0+(?=\d)/, '') || '0';
  const frac = (fracPart ?? '').replace(/0+$/, '');

  return { negative: sign === '-' && !(int === '0' && frac === ''), int, frac };
}

export function toDecimalString({ negative, int, frac }: DecimalParts): string {
  const body = frac ? `${int}.${frac}` : int;
  return negative ? `-${body}` : body;
}

/** Сдвигает десятичную запятую вправо на `places` знаков (×10^places). */
export function shiftDecimal(value: string, places: number): string {
  const { negative, int, frac } = parseDecimal(value);

  const digits = int + frac;
  const pointAt = int.length + places;

  let result: string;
  if (pointAt <= 0) {
    result = `0.${'0'.repeat(-pointAt)}${digits}`;
  } else if (pointAt >= digits.length) {
    result = digits + '0'.repeat(pointAt - digits.length);
  } else {
    result = `${digits.slice(0, pointAt)}.${digits.slice(pointAt)}`;
  }

  return toDecimalString({ ...parseDecimal(result), negative });
}

/** Округляет до `digits` знаков после запятой, half-up по модулю. */
export function roundDecimal(value: string, digits: number): DecimalParts {
  const { negative, int, frac } = parseDecimal(value);

  if (frac.length <= digits) {
    return { negative, int, frac: frac.padEnd(digits, '0') };
  }

  const keep = frac.slice(0, digits);
  const nextDigit = Number(frac.charAt(digits));

  if (nextDigit < 5) {
    return { negative, int, frac: keep };
  }

  // Перенос разряда выполняется над строкой: сложение в number снова
  // вернуло бы потерю точности на больших суммах.
  const carried = incrementDigits(int + keep);
  const intLength = carried.length - digits;

  return {
    negative,
    int: carried.slice(0, intLength) || '0',
    frac: carried.slice(intLength),
  };
}

function incrementDigits(digits: string): string {
  const chars = digits.split('');
  let index = chars.length - 1;

  while (index >= 0) {
    const digit = Number(chars[index]) + 1;
    if (digit < 10) {
      chars[index] = String(digit);
      return chars.join('');
    }
    chars[index] = '0';
    index -= 1;
  }

  return `1${chars.join('')}`;
}

export function isNegative(value: string): boolean {
  return parseDecimal(value).negative;
}

export function isZero(value: string): boolean {
  const { int, frac } = parseDecimal(value);
  return int === '0' && frac === '';
}
