/**
 * Форматирование раздела «Ранжирование».
 *
 * Отдельно от `market-format.ts`: там величины покрытия — доли и счётчики
 * сессий, здесь — длительности прогонов и моменты времени. Правила вывода
 * перенесены из артефакта Open Design.
 *
 * **Пояс не задаётся.** Моменты приходят с сервера со смещением, и перевод в
 * пояс зрителя делает браузер (FR-073). Прежде здесь стоял `Europe/Moscow` без
 * подписи «мск» — в отличие от соседнего модуля, где подпись была, — и человек
 * вне Москвы читал чужое время как своё. Пояс биржи остаётся внутри правил
 * системы и на экран не выносится (FR-074).
 */

import { DASH } from './market-format';

/** Время начала прогона: часы и минуты. */
export function formatClock(value: string | null | undefined): string {
  if (!value) return DASH;
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

/** Момент целиком: дата и время. */
export function formatStamp(value: string | null | undefined): string {
  if (!value) return DASH;
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

/** Длительность прогона как `мм:сс`. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return DASH;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

/** Длительность в секундах с одним знаком: для идущего прогона. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return DASH;
  return `${seconds.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с`;
}

/**
 * Ожидаемая длительность по прошлым прогонам.
 *
 * Это **оценка, а не срок окончания**, и текст обязан так и звучать: сколько
 * займёт текущий прогон, заранее не знает никто. По одному прогону диапазон не
 * строится — одно наблюдение не даёт разброса, и показывать его как ориентир
 * было бы обещанием.
 */
export function formatDurationReference(durations: number[]): string | null {
  const samples = durations.filter((value) => Number.isFinite(value) && value >= 0);
  if (samples.length < 2) return null;

  const low = Math.min(...samples);
  const high = Math.max(...samples);
  const range =
    low === high ? formatSeconds(low) : `${formatDuration(low)}–${formatDuration(high)}`;
  return `Предыдущие занимали ${range} · по последним ${samples.length} завершённым прогонам. Ориентир, не срок окончания.`;
}
