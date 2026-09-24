/**
 * Ответы раздела «Рыночные данные» для тестов.
 *
 * Числа взяты из снимка стенда на 2026-09-03, приведённого в брифе дизайна:
 * котировки 255/314 и 96,1% строк со значениями, агрегаты 224/314, глобальные
 * ряды 18/314, позиции 9/82. Строка `positions` в сценарии аномалии
 * воспроизводит настоящий дефект: покрытие 82/82 при нулевой доле значений.
 */

import type {
  CatchupStateDto,
  CatchupStatus,
  CoverageDto,
  GroupCoverageDto,
  SourceCoverageDto,
} from '@/entities/market-data';

/**
 * Источники группы — те же, что у сборщика.
 *
 * Держать их здесь списком приходится: сводка приходит с сервера целиком, и
 * подделать её частично значило бы проверять форму, которой не существует.
 */
const SOURCES: Record<GroupCoverageDto['group'], [string, string, string][]> = {
  quotes: [['equity_d1', 'Котировки доски TQBR', 'session']],
  aggregates: [['equity_agg', 'Агрегаты доски TQBR', 'session']],
  global: [
    ['global_series', 'Глобальные ряды', 'session'],
    ['cbr', 'Курсы и ставка ЦБ', 'session'],
    ['brent', 'Brent', 'session'],
    ['index_constituents', 'Состав индекса', 'session'],
  ],
  positions: [['futures_positions', 'Позиции по фьючерсам', 'session']],
  fund_quotes: [['equity_d1', 'Котировки доски TQBR', 'session']],
  fund_aggregates: [['equity_agg', 'Агрегаты доски TQBR', 'session']],
  reference: [
    ['equity_sectors', 'Секторы бумаг', 'daily'],
    ['equity_lot_sizes', 'Лоты бумаг', 'daily'],
  ],
};

function sourcesOf(group: GroupCoverageDto['group'], covered: number): SourceCoverageDto[] {
  return SOURCES[group].map(([source_id, title, scope]) => ({
    source_id,
    title,
    scope,
    status: 'ok',
    state: 'complete',
    sessions_covered: covered,
    requires_audit: 0,
    failures: [],
    failures_total: 0,
    last_checked_at: scope === 'daily' ? '2026-09-03T18:00:00Z' : null,
    reason: null,
  }));
}

export function coverageFixture(overrides: Partial<CoverageDto> = {}): CoverageDto {
  return {
    asof_date: '2026-09-03',
    next_session: '2026-09-03',
    ingest_after_close: '19:30',
    catchup_window: {
      date_from: '2026-04-20',
      date_till: '2026-09-03',
      sessions: 314,
    },
    universe: { assets: 243, assets_with_futures: 63, asof_date: '2026-09-03' },
    groups: [
      historyGroup('quotes', 'котировки', 255, 314, 0.961),
      historyGroup('aggregates', 'агрегаты', 224, 314, 1.0),
      historyGroup('global', 'глобальные ряды', 18, 314, 1.0),
      historyGroup('positions', 'позиции по фьючерсам', 9, 82, 1.0),
      {
        group: 'reference',
        title: 'справочники',
        has_history: false,
        rows_total: 506,
        rows_with_values: 506,
        value_ratio: 1.0,
        looks_collected_but_empty: false,
        sources: sourcesOf('reference', 0),
        state: 'complete',
        latest_failure: null,
      },
    ],
    ...overrides,
  };
}

/** Группа фондов: окно с 22.06.2026, во вход модели не идёт (FR-060). */
export function fundGroup(
  group: 'fund_quotes' | 'fund_aggregates',
  title: string,
): GroupCoverageDto {
  return {
    ...historyGroup(group, title, 68, 68, 0.72),
    period_from: '2026-06-22',
    model_input: false,
  };
}

function historyGroup(
  group: GroupCoverageDto['group'],
  title: string,
  covered: number,
  window: number,
  valueRatio: number | null,
): GroupCoverageDto {
  return {
    group,
    title,
    has_history: true,
    window_sessions: window,
    sessions_covered: covered,
    coverage_ratio: Number((covered / window).toFixed(4)),
    period_from: '2025-06-10',
    period_till: '2026-09-03',
    gaps: window - covered,
    // Абсолютных чисел строк в снимке стенда нет: интерфейс показывает
    // прочерк, а не выдумывает значение (FR-015).
    rows_total: null,
    rows_with_values: null,
    value_ratio: valueRatio,
    looks_collected_but_empty: false,
    sources: sourcesOf(group, covered),
    // Состояние выбирает сервер (FR-024e); у подделки — по недостающим сессиям.
    state: covered < window ? 'missing' : 'complete',
    latest_failure: null,
  };
}

/** Сводка, в которой одна группа покрыта полностью и пуста (FR-016). */
export function anomalyCoverageFixture(): CoverageDto {
  const report = coverageFixture();
  const groups = report.groups.map((row) =>
    row.group === 'positions'
      ? {
          ...row,
          window_sessions: 82,
          sessions_covered: 82,
          coverage_ratio: 1.0,
          gaps: 0,
          period_from: '2026-05-08',
          period_till: '2026-09-02',
          value_ratio: 0.0,
          looks_collected_but_empty: true,
          state: 'empty' as const,
        }
      : row,
  );
  return { ...report, groups };
}

export function catchupFixture(
  status: CatchupStatus = 'idle',
  overrides: Partial<CatchupStateDto> = {},
): CatchupStateDto {
  const base: CatchupStateDto = {
    status,
    mode: 'daily',
    groups: [],
    date_from: null,
    date_till: null,
    clamped: false,
    sessions: {
      requested: 0,
      collected: 0,
      partial: 0,
      failed: 0,
      skipped: 0,
      pending: 0,
      outcomes: [],
    },
    skips: [],
    skips_total: 0,
    log: [],
    current: null,
    started_at: null,
    finished_at: null,
    last_response_at: null,
    stop_requested: false,
    reason: null,
    requested: 0,
    closed: 0,
    failed: 0,
    remaining: 0,
  };

  // Счётчик пропусков идёт за самим списком: тест, задающий пропуски, не
  // должен помнить про второе поле, иначе подпись разойдётся с данными.
  const withTotals = (state: CatchupStateDto): CatchupStateDto => ({
    ...state,
    skips_total: state.skips_total || state.skips.length,
  });

  if (status === 'idle') return withTotals({ ...base, ...overrides });

  const active = status === 'running' || status === 'stopping';

  return withTotals({
    ...base,
    groups: ['quotes', 'aggregates', 'global', 'positions', 'reference'],
    date_from: '2026-04-20',
    date_till: '2026-09-03',
    sessions: {
      requested: 90,
      collected: 18,
      partial: 0,
      failed: 1,
      skipped: 0,
      pending: 71,
      outcomes: [
        { session_date: '2026-04-20', outcome: 'collected' },
        { session_date: '2026-04-21', outcome: 'failed' },
      ],
    },
    current: active
      ? {
          session_date: '2026-05-14',
          sources: [
            {
              source_id: 'trading_calendar',
              title: 'Торговый календарь',
              scope: 'daily',
              state: 'done',
            },
            {
              source_id: 'equity_d1',
              title: 'Котировки доски TQBR',
              scope: 'session',
              state: 'done',
            },
            {
              source_id: 'equity_agg',
              title: 'Агрегаты доски TQBR',
              scope: 'session',
              state: 'running',
            },
            {
              source_id: 'futures_positions',
              title: 'Позиции по фьючерсам',
              scope: 'session',
              state: 'pending',
            },
          ],
        }
      : null,
    started_at: '2026-09-10T09:12:04Z',
    finished_at: active ? null : '2026-09-10T09:40:00Z',
    // У идущего прогона момент последнего ответа источника есть всегда: им
    // отличают долгий источник от зависшего.
    last_response_at: active ? '2026-09-10T09:39:52Z' : null,
    requested: 90,
    closed: 18,
    failed: 1,
    remaining: 71,
    ...overrides,
  });
}
