/** Запросы раздела «Ранжирование» к Backend-API. */

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';

import { apiGet, apiPost, apiPut } from '@/shared/api/client';

import type { DailyMlStatusDto, RunDetailDto, RunHistoryDto, RunStatus } from './types';

export const statusQueryKey = ['daily-ml', 'status'] as const;
export const runsQueryKey = ['daily-ml', 'runs'] as const;
export const runQueryKey = (id: number) => ['daily-ml', 'run', id] as const;

/**
 * Частота чтения состояния, пока прогон идёт.
 *
 * Инференс эмулятора занимает секунды, настоящей модели — дольше. Три секунды
 * дают увидеть переход состояний и не превращают экран в поток запросов.
 */
export const RUNNING_POLL_MS = 3000;

export function fetchStatus(): Promise<DailyMlStatusDto> {
  return apiGet<DailyMlStatusDto>('/api/daily-ml/status');
}

export function useDailyMlStatus(): UseQueryResult<DailyMlStatusDto> {
  return useQuery({
    queryKey: statusQueryKey,
    queryFn: fetchStatus,
    // Опрос идёт, пока идёт прогон: в остальных состояниях новых чисел не
    // появится, пока человек или планировщик чего-нибудь не сделают.
    refetchInterval: (query) => (query.state.data?.status === 'running' ? RUNNING_POLL_MS : false),
  });
}

export function useRunHistory(
  status: RunStatus | 'all',
  limit: number,
  offset: number,
): UseQueryResult<RunHistoryDto> {
  return useQuery({
    queryKey: [...runsQueryKey, status, limit, offset],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (status !== 'all') params.set('status', status);
      return apiGet<RunHistoryDto>(`/api/daily-ml/runs?${params.toString()}`);
    },
  });
}

export function useRunDetail(id: number | null): UseQueryResult<RunDetailDto> {
  return useQuery({
    queryKey: runQueryKey(id ?? 0),
    queryFn: () => apiGet<RunDetailDto>(`/api/daily-ml/runs/${id}?limit=500`),
    enabled: id !== null,
  });
}

export function useReconcile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiPost<{ queued: number; already_up_to_date: boolean }>('/api/daily-ml/reconcile'),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: statusQueryKey });
      void client.invalidateQueries({ queryKey: runsQueryKey });
    },
  });
}

export function useSetPaused() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (paused: boolean) =>
      apiPut<{ paused: boolean }>('/api/daily-ml/settings', { paused }),
    onSuccess: () => client.invalidateQueries({ queryKey: statusQueryKey }),
  });
}

export function useRetryRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost<{ status: string }>(`/api/daily-ml/runs/${id}/retry`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: statusQueryKey });
      void client.invalidateQueries({ queryKey: runsQueryKey });
    },
  });
}
