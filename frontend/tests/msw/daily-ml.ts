/**
 * Ответы раздела «Ранжирование» для тестов.
 *
 * Формы взяты из `specs/007-daily-ml-lifecycle/contracts/daily-ml-lifecycle-api.md`.
 * Даты — сентябрь 2026, как на стенде: 11.09 — последняя закрытая сессия,
 * 12.09 — текущий день.
 */

import type { DailyMlStatusDto, RunDetailDto, RunRowDto } from '@/entities/daily-ml';

export function statusFixture(overrides: Partial<DailyMlStatusDto> = {}): DailyMlStatusDto {
  return {
    status: 'up_to_date',
    paused: false,
    latest_data_ready: '2026-09-11',
    latest_ml_success: '2026-09-11',
    current: null,
    queue: [],
    queue_progress: { completed: 0, total: 0 },
    last_error: null,
    next_check_at: '2026-09-12T16:30:00+00:00',
    data_gap_sessions: 0,
    readiness_known: true,
    blocking_groups: [],
    stale_latest: false,
    model_id: 'daily-ml-emulator',
    model_version: 'emulator-v1',
    emulated: true,
    ...overrides,
  };
}

export function runFixture(overrides: Partial<RunRowDto> = {}): RunRowDto {
  return {
    id: 143,
    asof_date: '2026-09-11',
    status: 'success',
    model_id: 'daily-ml-emulator',
    model_version: 'emulator-v1',
    attempt: 1,
    started_at: '2026-09-11T17:30:04+00:00',
    finished_at: '2026-09-11T17:30:06+00:00',
    duration_seconds: 2,
    included_asset_count: 288,
    error_code: null,
    error_message: null,
    emulated: true,
    ...overrides,
  };
}

export function runDetailFixture(overrides: Partial<RunDetailDto> = {}): RunDetailDto {
  const row = runFixture(overrides as Partial<RunRowDto>);
  return {
    ...row,
    input: {
      dataset_digest: 'sha256:9f2c00',
      dataset_ref: 'file:///datasets/2026-09-11-9f2c00',
      dataset_available: true,
      window_from: '2025-06-11',
      window_till: '2026-09-11',
      complete: true,
    },
    stale: false,
    items: [
      { rank: 1, asset_id: 'EQ_AST_SBER', price_series_id: 'EQ_PRS_SBER', score: '0.996528' },
      { rank: 2, asset_id: 'EQ_AST_LKOH', price_series_id: 'EQ_PRS_LKOH', score: '0.884100' },
    ],
    ...overrides,
  };
}

export function historyFixture(items: RunRowDto[], total = items.length) {
  return { total, items };
}
