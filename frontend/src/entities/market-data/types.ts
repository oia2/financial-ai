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

/**
 * Исход одного источника группы за окно.
 *
 * Полнота группы считается по каждому источнику, поэтому неполнота обязана
 * называть источник: у «глобальных рядов» их четыре, и ошибка одного — это
 * ошибка конкретного ряда, а не группы вообще (FR-032).
 */
export interface SourceCoverageDto {
  source_id: string;
  title: string;
  /** `session`, `period` или `daily` — как часто источник ходит за данными. */
  scope: string;
  /**
   * Три состояния, а не два.
   *
   * `partial` — окно покрыто не целиком, но источник не падал: обычное
   * состояние недособранного окна, оно ничего не требует. `failed` — были
   * записанные неудачи, и они названы в `failures`. Пока состояний было два,
   * экран называл ошибкой всякую неполноту и не мог показать причину, потому
   * что причины не было.
   */
  status: 'ok' | 'partial' | 'failed';
  sessions_covered: number;
  /**
   * Неудачи по дням: что именно и когда сломалось.
   *
   * «Ошибка источника» без дня и причины — состояние, с которым человеку
   * нечего делать: проверить у источника нечего и решить, ждать или
   * вмешиваться, не по чему.
   */
  failures: { session_date: string; reason: string | null }[];
  /** Сколько неудач всего: список ограничен, и молчать об остатке нельзя. */
  failures_total: number;
}

/**
 * Состав бумаг на дату сводки.
 *
 * Знаменатель неполноты позиций: фьючерс есть не у каждой бумаги, и его
 * отсутствие — не пропуск (FR-010, FR-013).
 */
export interface UniverseDto {
  assets: number;
  assets_with_futures: number;
  /**
   * Сессия, по которой посчитан состав.
   *
   * Может быть старше даты сводки: состав считается по последней собранной
   * сессии, иначе несобранный день давал бы «0 бумаг» и выглядел бы
   * отсутствием торгов (FR-019a).
   */
  asof_date: string | null;
}

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

  /** Исход каждого источника группы. Пуст, когда окно пусто. */
  sources: SourceCoverageDto[];
}

export interface CatchupWindowDto {
  date_from: string | null;
  date_till: string | null;
  sessions: number;
}

export interface CoverageDto {
  asof_date: string;
  /** Сессия, которую возьмёт следующий сбор. Из торгового календаря (FR-024a). */
  next_session: string | null;
  /** Порог сбора текущей сессии по биржевому времени, «19:30». */
  ingest_after_close: string;
  /** Окно догона — не окно группы: у групп они разные. */
  catchup_window: CatchupWindowDto;
  /** Сколько бумаг и у скольких из них есть фьючерс. */
  universe: UniverseDto;
  groups: GroupCoverageDto[];
}

/**
 * Идёт ли автоматический сбор рыночных данных.
 *
 * Это НЕ пауза ранжирования: переключатели разные, и один другой не заменяет.
 * Пауза ранжирования сбор данных не останавливает и никогда не останавливала.
 */
export interface CollectionSettingsDto {
  paused: boolean;
}

export type CatchupStatus =
  'idle' | 'running' | 'stopping' | 'stopped' | 'finished' | 'failed' | 'interrupted';

/** Ежедневный сбор или ручной. У режимов РАЗНЫЕ планы источников (FR-004). */
export type RunMode = 'daily' | 'manual';

/** Исход сессии плана. */
export type SessionOutcome = 'collected' | 'partial' | 'failed' | 'skipped';

/**
 * Когда источник выполняется.
 *
 * `session` — на каждую сессию, `period` — один раз на весь период догона,
 * `daily` — раз в сутки. В счётчик источников сессии входят только первые:
 * иначе счётчик обещал бы, что остальные повторятся на следующий день (FR-007).
 */
export type SourceScope = 'session' | 'period' | 'daily';

export type SourceState = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface RunSourceDto {
  source_id: string;
  /** Имя для человека. Своей таблицы имён у интерфейса нет. */
  title: string;
  scope: SourceScope;
  state: SourceState;
  /** Подробность: сколько бумаг, какой инструмент, причина неудачи. */
  detail?: string;
}

/** Причина, по которой сессия не взята в работу. Перечень закрытый (FR-002). */
export type SkipReason =
  'withheld_until_close' | 'retry_delay' | 'attempts_exhausted' | 'gap_over_limit';

export interface SessionSkipDto {
  session_date: string;
  reason: SkipReason;
  detail: string | null;
}

export interface SessionProgressDto {
  requested: number;
  collected: number;
  partial: number;
  failed: number;
  skipped: number;
  pending: number;
  outcomes: { session_date: string; outcome: SessionOutcome }[];
}

export interface CatchupStateDto {
  status: CatchupStatus;
  /** Режим прогона. Состав плана интерфейс не задаёт (FR-004). */
  mode: RunMode;
  groups: GroupId[];
  /** Принятый диапазон: первая и последняя сессия плана. */
  date_from: string | null;
  date_till: string | null;
  clamped: boolean;
  sessions: SessionProgressDto;
  skips: SessionSkipDto[];
  /** Сколько пропусков всего: список ограничен полусотней. */
  skips_total: number;
  /** Журнал событий прогона, свежие сверху. Только пока прогон идёт. */
  log: { at: string; text: string }[];
  /** Текущая сессия и план её источников в порядке выполнения (FR-003). */
  current: { session_date: string; sources: RunSourceDto[] } | null;
  started_at: string | null;
  finished_at: string | null;
  last_response_at: string | null;
  stop_requested: boolean;
  /** Причина прерывания. Формулирует сервер, не интерфейс (FR-034). */
  reason: string | null;

  /** Поля контракта фичи 005. Остаются, пока раздел не переехал целиком. */
  requested: number;
  closed: number;
  failed: number;
  remaining: number;
}

/** Итог прогона из журнала. Переживает перезапуск сборщика (FR-005). */
export interface RunSummaryDto {
  run_id: string;
  mode: RunMode;
  started_at: string;
  finished_at: string | null;
  status: 'finished' | 'failed' | 'interrupted';
  sessions: { requested: number; collected: number; failed: number; skipped: number };
  failures: {
    source_id: string;
    title: string;
    session_date: string | null;
    reason: string | null;
  }[];
}

/**
 * Изменение состава инструментов.
 *
 * Без него рост или убыль числа собранных бумаг выглядели бы пропуском сбора
 * (FR-016).
 */
export interface LinkEventDto {
  at: string;
  ticker: string;
  kind: 'opened' | 'changed' | 'closed';
  contract_code: string;
  detail: string;
}

export interface RunsDto {
  runs: RunSummaryDto[];
  events: LinkEventDto[];
  /**
   * Причины пропусков из хранилища.
   *
   * Ход прогона живёт в памяти сборщика и исчезает с перезапуском, а причина
   * обязана жить дольше: без неё человек видит дыру и не знает, ждать ему или
   * вмешиваться (FR-002).
   */
  skips: (SessionSkipDto & { decided_at: string })[];
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

/** День календаря сессий. Будущее сервер не утверждает: `kind` там `future`. */
export type CalendarDayKind = 'session' | 'nontrade' | 'open' | 'future';

export interface CalendarDayDto {
  date: string;
  kind: CalendarDayKind;
  /** Состояние по каждой группе: `collected` или `missing`. */
  groups: Record<string, 'collected' | 'missing'>;
}

export interface CalendarMonthDto {
  month: string;
  today: string;
  days: CalendarDayDto[];
}
