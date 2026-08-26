/** Запросы к Backend-API и производные от них состояния раздела. */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import { apiGet, ServerUnreachableError } from '@/shared/api/client';
import { isZero } from '@/shared/lib/decimal';

import type { PortfolioDto, PortfolioViewState } from './types';

export const portfolioQueryKey = ['portfolio'] as const;

/**
 * Частота опроса API развязана с частотой обращения worker'а к брокеру.
 *
 * Worker ходит в T-Bank раз в настроенный интервал; интерфейс читает уже
 * сохранённое состояние — это дешёвое чтение из PostgreSQL. Опрашивая в
 * десять раз чаще, интерфейс показывает свежие данные с задержкой не более
 * 10% интервала (SC-010). Границы не дают выродиться в шквал запросов при
 * малом интервале и в редкий опрос при большом.
 */
export const MIN_POLL_SECONDS = 3;
export const MAX_POLL_SECONDS = 30;

export function pollIntervalSeconds(refreshIntervalSeconds: number): number {
  const derived = refreshIntervalSeconds / 10;
  return Math.min(MAX_POLL_SECONDS, Math.max(MIN_POLL_SECONDS, derived));
}

export function fetchPortfolio(): Promise<PortfolioDto> {
  return apiGet<PortfolioDto>('/api/portfolio');
}

export function usePortfolioQuery(): UseQueryResult<PortfolioDto, Error> {
  return useQuery({
    queryKey: portfolioQueryKey,
    queryFn: fetchPortfolio,
    refetchInterval: (query) => {
      const interval = query.state.data?.sync.refresh_interval_seconds;
      if (interval === undefined) return MIN_POLL_SECONDS * 1000;
      return pollIntervalSeconds(interval) * 1000;
    },
    // Обрыв связи не должен стирать последние известные данные с экрана.
    placeholderData: (previous) => previous,
  });
}

/**
 * Состояние раздела (FR-015).
 *
 * Обрыв связи с сервером определяется отсутствием ответа, а не полем внутри
 * него: одна причина не может быть выдана за другую (FR-037).
 */
export function selectPortfolioState(
  data: PortfolioDto | undefined,
  error: Error | null,
  isPending: boolean,
): PortfolioViewState {
  if (error instanceof ServerUnreachableError) return 'server-offline';
  if (isPending || data === undefined) return 'loading';

  if (data.broker.status !== 'connected') return 'no-broker';
  if (data.snapshot === null) return 'sync-error';

  if (data.sync.status === 'failed') return 'sync-error';
  if (data.sync.is_stale) return 'stale';

  const isEmpty = data.snapshot.positions_count === 0 && isZero(data.snapshot.cash);
  if (isEmpty) return 'empty';

  return 'fresh';
}
