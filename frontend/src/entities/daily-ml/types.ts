/**
 * Типы раздела «Ранжирование».
 *
 * Форма задана контрактом `specs/007-daily-ml-lifecycle/contracts/`.
 *
 * Два правила видны прямо в типах:
 *
 *  - **две даты, а не одна.** `latest_data_ready` и `latest_ml_success` —
 *    разные величины, и сливать их нельзя: именно их расхождение показывает,
 *    что модель отстаёт;
 *  - **времени начала расчёта нет.** Есть `next_check_at` — срок, после
 *    которого собираются данные завершившейся сессии. Периодичностью проверки
 *    он не является: ранжирование ищет работу непрерывно. Когда начнётся
 *    расчёт, заранее неизвестно, и поля для этого не существует намеренно.
 *    Момент приходит со смещением пояса биржи, а показывается в поясе
 *    зрителя — перевод делает браузер (FR-073, FR-074).
 */

export type RunStatus = 'queued' | 'running' | 'success' | 'failed';

export type SectionStatus =
  'up_to_date' | 'waiting' | 'running' | 'lagging' | 'failed' | 'paused' | 'data_gap';

export interface CurrentRunDto {
  asof_date: string;
  started_at: string | null;
  attempt: number;
}

export interface QueueItemDto {
  asof_date: string;
  status: RunStatus;
}

export interface DailyMlStatusDto {
  status: SectionStatus;
  paused: boolean;
  latest_data_ready: string | null;
  latest_ml_success: string | null;
  current: CurrentRunDto | null;
  queue: QueueItemDto[];
  /**
   * Счётный прогресс очереди. Считает сервер: это факт о хранилище, а не
   * наблюдение экрана, и перезагрузка страницы его не меняет.
   */
  queue_progress: { completed: number; total: number };
  last_error: string | null;
  /** Срок сбора данных сессии. НЕ периодичность проверки и НЕ начало расчёта. */
  next_check_at: string;
  data_gap_sessions: number;
  /**
   * Ответил ли сборщик. `false` означает «готовность неизвестна» — это не то
   * же самое, что «готовой даты нет», и выдавать одно за другое нельзя.
   */
  readiness_known: boolean;
  /**
   * Обязательные группы, из-за которых дата не готова. Пустой список — вход
   * полон. Название приходит с сервера: второго объявления одного факта в
   * браузере не заводится.
   */
  blocking_groups: Array<{ group: string; title: string }>;
  /**
   * Для последней готовой даты вход изменился после успеха. `null` — признак
   * не рассчитан: «не знаю» не выдаётся за «не устарел».
   */
  stale_latest: boolean | null;
  model_id: string | null;
  model_version: string | null;
  /** Результат получен эмулятором, а не моделью. */
  emulated: boolean | null;
}

export interface RunRowDto {
  id: number;
  asof_date: string;
  status: RunStatus;
  model_id: string;
  model_version: string;
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  included_asset_count: number | null;
  error_code: string | null;
  error_message: string | null;
  emulated: boolean | null;
}

export interface RunHistoryDto {
  total: number;
  items: RunRowDto[];
}

export interface RankingItemDto {
  rank: number;
  asset_id: string;
  price_series_id: string;
  /** Строка: скор участвует в сортировке, и `number` исказил бы порядок. */
  score: string;
}

export interface RunDetailDto extends RunRowDto {
  input: {
    dataset_digest: string;
    dataset_ref: string;
    /** Набор удалён ретеншеном: результат остаётся, повтор невозможен. */
    dataset_available: boolean;
    /**
     * Окно и полнота читаются из прогона, а не считаются заново: глубина окна —
     * настройка, полнота входа меняется с приходом данных.
     */
    window_from: string | null;
    window_till: string | null;
    complete: boolean | null;
  };
  /** `null` — признак не рассчитан. Считается только для последней готовой даты. */
  stale: boolean | null;
  items: RankingItemDto[];
}

export function isActive(status: SectionStatus): boolean {
  return status === 'running';
}
