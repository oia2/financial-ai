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

/** Дата ISO в русском виде: `2026-09-03` → `03.09.2026`. */
export function formatIsoDate(value: string | null | undefined): string {
  if (!value) return DASH;
  return value.slice(0, 10).split('-').reverse().join('.');
}

/** Дата сбора с порогом: для московской даты сегодня вместо числа пишется «сегодня». */
export function formatCollectionStart(
  sessionDate: string | null | undefined,
  localTime: string | null | undefined,
): string {
  if (!sessionDate) return DASH;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? '';
  const today = `${value('year')}-${value('month')}-${value('day')}`;
  const date = sessionDate.slice(0, 10) === today ? 'сегодня' : formatIsoDate(sessionDate);
  return localTime ? `${date} после ${localTime}` : date;
}

/**
 * Отметка времени прогона без года: день, месяц, часы, минуты.
 *
 * Год опущен намеренно — догон показывается по свежим прогонам. Именно этим
 * функция отличается от `formatStamp` в `daily-ml-format.ts`, и потому носит
 * своё имя: два разных вывода под одним именем однажды разойдутся молча.
 *
 * Пояс не задаётся и подписи не имеет: момент приходит со смещением, перевод
 * делает браузер (FR-073). Подпись «мск» здесь была, а в соседнем модуле её
 * забыли — одно и то же время в двух разделах читалось по-разному.
 */
export function formatShortStamp(value: string | null | undefined): string {
  if (!value) return DASH;
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

/** Целое число с разделителями разрядов. */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return value.toLocaleString('ru-RU');
}

/**
 * Длительность между двумя моментами: `03:24`, а при часах — `1:03:24`.
 *
 * Факт, а не обещание: сколько прогон ИДЁТ или сколько он ШЁЛ. Оценок того,
 * сколько он ещё продлится, раздел не делает (FR-027).
 */
export function formatDuration(from: string | null, to: string | null): string {
  if (from === null) return DASH;
  const start = Date.parse(from);
  const end = to === null ? Date.now() : Date.parse(to);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return DASH;

  const total = Math.floor((end - start) / 1000);
  const parts = [Math.floor(total / 60) % 60, total % 60].map((n) => String(n).padStart(2, '0'));
  const hours = Math.floor(total / 3600);
  return hours > 0 ? `${hours}:${parts.join(':')}` : parts.join(':');
}

/** Сколько секунд назад это было: `8 с назад`. */
export function formatAgo(value: string | null): string {
  if (value === null) return DASH;
  const at = Date.parse(value);
  if (Number.isNaN(at)) return DASH;

  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (seconds < 90) return `${seconds} с назад`;
  return `${Math.round(seconds / 60)} мин назад`;
}

/**
 * Существительное в форме, согласованной с числом.
 *
 * Перенесено из артефакта Open Design `market-data.html`: подписи прогона
 * читаются как фраза, и «дальше ещё 3 сессия» её рушит.
 */
export function plural(count: number, one: string, few: string, many: string): string {
  const tail = count % 10;
  const hundred = count % 100;
  if (tail === 1 && hundred !== 11) return one;
  if (tail >= 2 && tail <= 4 && (hundred < 12 || hundred > 14)) return few;
  return many;
}
