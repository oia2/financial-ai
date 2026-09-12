/**
 * Форматирование раздела «Рыночные данные».
 *
 * Отдельно от `format.ts`: там значения приходят строками десятичных дробей
 * (деньги и количества, где `float` запрещён на всём пути), здесь — обычными
 * числами и датами ISO. Правила вывода перенесены из артефакта Open Design.
 */

/** Прочерк вместо отсутствующего значения. Не ноль: ноль означал бы «пусто». */
export const DASH = '—';

/**
 * Доля единицы в проценты с одним знаком: `0.812` → `81,2%`.
 *
 * `null` — значение не передано, и на экране остаётся прочерк (FR-015).
 */
export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return `${(value * 100).toLocaleString('ru-RU', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`;
}

/** Ширина полосы в процентах: доля единицы, ограниченная диапазоном 0…100. */
export function ratioWidth(value: number | null | undefined): string {
  if (value === null || value === undefined) return '0%';
  return `${Math.min(100, Math.max(0, value * 100))}%`;
}

/** Дата ISO в русском виде: `2026-09-03` → `03.09.2026`. */
export function formatIsoDate(value: string | null | undefined): string {
  if (!value) return DASH;
  return value.slice(0, 10).split('-').reverse().join('.');
}

/** Отметка времени прогона в московском времени, как в артефакте. */
export function formatMoscowStamp(value: string | null | undefined): string {
  if (!value) return DASH;
  const formatted = new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }).format(new Date(value));
  return `${formatted} мск`;
}

/** Целое число с разделителями разрядов. */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return value.toLocaleString('ru-RU');
}
