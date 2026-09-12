/**
 * Типы ответов раздела «Рыночные данные».
 *
 * Форма задана контрактом фичи 005 и передаётся Backend-API как есть.
 *
 * Два правила, которые здесь видны в типах и которые нельзя терять при
 * отображении:
 *
 *  - у группы **без истории** полей окна и покрытия НЕТ вовсе, а не `null`:
 *    ноль или прочерк на их месте читался бы как «ничего не собрано»
 *    (FR-014). Поэтому они необязательные (`?`), а не `| null`;
 *  - `rows_total`, `rows_with_values` и `value_ratio` могут быть `null` —
 *    это «значение не передано», и показывается оно прочерком с пояснением,
 *    а не нулём (FR-015).
 */

export type GroupId = 'quotes' | 'aggregates' | 'global' | 'positions' | 'reference';

export interface GroupCoverageDto {
  group: GroupId;
  /** Название группы. Показывается с заглавной буквы, своей таблицы меток нет. */
  title: string;
  has_history: boolean;

  /** Поля истории. Отсутствуют целиком при `has_history: false`. */
  window_sessions?: number;
  sessions_covered?: number;
  /** Доля единицы; `null`, если окно пусто. */
  coverage_ratio?: number | null;
  period_from?: string | null;
  period_till?: string | null;
  /** Непокрытые сессии окна. */
  gaps?: number;

  rows_total: number | null;
  rows_with_values: number | null;
  value_ratio: number | null;

  /**
   * Покрытие есть, значений практически нет.
   *
   * Вывод делает сервер: порог не должен жить второй жизнью в интерфейсе
   * (FR-016, research.md R2.1).
   */
  looks_collected_but_empty: boolean;
}

export interface CatchupWindowDto {
  date_from: string | null;
  date_till: string | null;
  sessions: number;
}

export interface CoverageDto {
  asof_date: string;
  /** Окно догона — не окно группы: у групп они разные. */
  catchup_window: CatchupWindowDto;
  groups: GroupCoverageDto[];
}

export type CatchupStatus = 'idle' | 'running' | 'stopping' | 'stopped' | 'finished' | 'failed';

export interface CatchupStateDto {
  status: CatchupStatus;
  groups: GroupId[];
  /** Принятый диапазон: первая и последняя сессия плана. */
  date_from: string | null;
  date_till: string | null;
  clamped: boolean;
  /** Число сессий плана. С сервера — суммой пропусков не вычисляется (FR-029). */
  requested: number;
  closed: number;
  /** Незакрывшиеся. В `closed` не входят и в индикатор хода — отдельным сегментом. */
  failed: number;
  remaining: number;
  current: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** Причина прерывания. Формулирует сервер, не интерфейс (FR-034). */
  reason: string | null;
}

export interface CatchupStartResultDto {
  status: CatchupStatus;
  groups: GroupId[];
  date_from?: string | null;
  date_till?: string | null;
  clamped?: boolean;
  requested_sessions: number;
  reason?: string | null;
}

/** Что человек ввёл до ответа сервера. Серверным полем не является (FR-040). */
export interface LaunchRequest {
  groups: GroupId[] | null;
  date_from: string | null;
  date_till: string | null;
}

export function isCatchupActive(status: CatchupStatus): boolean {
  return status === 'running' || status === 'stopping';
}
