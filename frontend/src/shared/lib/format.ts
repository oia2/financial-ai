/**
 * Форматирование под русскую локаль (FR-016).
 *
 * Округление до копеек выполняется здесь и только здесь — в БД и в ответах
 * API хранится и передаётся исходная точность до 10⁻⁹.
 */

import { isNegative, isZero, parseDecimal, roundDecimal, shiftDecimal } from './decimal';

/** Неразрывный пробел: разряды и знак валюты не должны переноситься. */
const NBSP = ' ';
const RUBLE = '₽';

function groupThousands(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

/**
 * Число с разделителями разрядов и запятой в дробной части.
 *
 * Незначащие нули в дробной части отбрасываются: дизайн форматирует суммы
 * через `Intl.NumberFormat` с `maximumFractionDigits`, то есть «40 545 ₽»,
 * а не «40 545,00 ₽». Копейки показываются, когда они есть.
 */
export function formatNumber(value: string, digits = 2): string {
  const { negative, int, frac } = roundDecimal(value, digits);
  const significant = frac.replace(/0+$/, '');
  const body = significant ? `${groupThousands(int)},${significant}` : groupThousands(int);
  return negative ? `−${body}` : body;
}

/** Денежное значение в рублях. */
export function formatMoney(value: string, digits = 2): string {
  return `${formatNumber(value, digits)}${NBSP}${RUBLE}`;
}

/** Денежное значение со знаком — для P&L (FR-013). */
export function formatSignedMoney(value: string, digits = 2): string {
  const formatted = formatMoney(value, digits);
  if (isNegative(value) || isZero(value)) {
    return formatted;
  }
  return `+${formatted}`;
}

/**
 * Доля единицы в проценты: `"0.0129"` → `"1,29%"`.
 * `null` на входе означает «процент не определён» и остаётся `null`.
 */
export function formatPercent(value: string | null, digits = 2): string | null {
  if (value === null) return null;
  return `${formatNumber(shiftDecimal(value, 2), digits)}%`;
}

/** Процент со знаком — для P&L. */
export function formatSignedPercent(value: string | null, digits = 2): string | null {
  const formatted = formatPercent(value, digits);
  if (formatted === null) return null;
  if (isNegative(value as string) || isZero(value as string)) {
    return formatted;
  }
  return `+${formatted}`;
}

/**
 * Количество: дробные части у долевых инструментов есть, но хвост из нулей
 * в таблице только мешает.
 */
export function formatQuantity(value: string): string {
  const { frac } = parseDecimal(value);
  return formatNumber(value, Math.min(frac.length, 6));
}

/**
 * Цена в таблице позиций: со знаком валюты, как в дизайне — «1 002 ₽».
 * `null` означает, что цена неизвестна, и остаётся `null`.
 */
export function formatPrice(value: string | null, digits = 2): string | null {
  if (value === null) return null;
  return formatMoney(value, digits);
}

/** Возраст данных словами: «Сейчас», «28 мин назад», «2 ч назад». */
export function formatAge(seconds: number): string {
  if (seconds < 60) return 'Сейчас';

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}${NBSP}мин назад`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}${NBSP}ч назад`;

  const days = Math.floor(hours / 24);
  return `${days}${NBSP}дн назад`;
}

/** Время последней синхронизации в часовом поясе пользователя. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** Дата и время — для подписи «Обновлено сегодня, 14:32:18». */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const sameDay =
    date.getFullYear() === today.getFullYear() &&
    date.getMonth() === today.getMonth() &&
    date.getDate() === today.getDate();

  const time = formatTime(iso);
  if (sameDay) return `сегодня, ${time}`;

  return `${date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })}, ${time}`;
}
