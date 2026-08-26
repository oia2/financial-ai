/**
 * Типы ответа Backend-API.
 *
 * Денежные и количественные значения — строки: точность из БД доходит до
 * интерфейса без потерь, округление выполняется только при отображении
 * (SC-002).
 */

export type BrokerStatus = 'connected' | 'not_configured' | 'rejected';

export type SyncStatus = 'ok' | 'failed';

export type FailureReasonCode =
  | 'broker_unavailable'
  | 'broker_rejected_token'
  | 'rate_limited'
  | 'validation_failed'
  | 'internal_error';

export interface AccountDto {
  display_name: string;
  /** Только маскированный вид (FR-022). */
  masked_id: string;
  currency: string;
}

export interface BrokerDto {
  status: BrokerStatus;
  account: AccountDto | null;
}

export interface PositionDto {
  instrument_uid: string;
  ticker: string | null;
  name: string | null;
  asset_type: string | null;
  currency: string;
  quantity: string;
  average_price: string | null;
  current_price: string;
  /** НКД на одну облигацию, уже включённый в value. У прочих инструментов «0». */
  accrued_interest: string;
  value: string;
  unrealized_pnl: string | null;
  /** Доля единицы; null — процент не определён, а не ноль. */
  unrealized_pnl_percent: string | null;
  share: string;
}

export interface SnapshotDto {
  captured_at: string;
  age_seconds: number;
  total_value: string;
  cash: string;
  cash_share: string;
  unrealized_pnl: string;
  unrealized_pnl_percent: string | null;
  positions_count: number;
  positions: PositionDto[];
}

export interface SyncDto {
  status: SyncStatus;
  last_success_at: string | null;
  last_attempt_at: string | null;
  failure_reason_code: FailureReasonCode | null;
  /** Считает backend по FR-040 — frontend эту логику не повторяет. */
  is_stale: boolean;
  stale_after_seconds: number;
  refresh_interval_seconds: number;
  in_progress: boolean;
}

export interface PortfolioDto {
  broker: BrokerDto;
  /** null, если успешной синхронизации ещё не было (US4 AS4). */
  snapshot: SnapshotDto | null;
  sync: SyncDto;
}

export interface RefreshResultDto {
  status: SyncStatus;
  deduplicated: boolean;
  captured_at: string | null;
  failure_reason_code: FailureReasonCode | null;
}

export interface RefreshIntervalDto {
  interval_seconds: number;
  min_seconds: number;
  max_seconds: number;
  default_seconds: number;
}

/**
 * Состояния раздела (FR-015). Седьмое, `server-offline`, выводится не из
 * тела ответа, а из его отсутствия — см. `selectPortfolioState`.
 */
export type PortfolioViewState =
  'loading' | 'no-broker' | 'empty' | 'fresh' | 'stale' | 'sync-error' | 'server-offline';
