/** Запросы раздела «Рыночные данные» к Backend-API. */

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';

import { apiDelete, apiGet, apiPost, apiPut } from '@/shared/api/client';

import {
  isCatchupActive,
  type CatchupStartResultDto,
  type CatchupStateDto,
  type CalendarMonthDto,
  type CollectionSettingsDto,
  type CoverageDto,
  type RunsDto,
  type LaunchRequest,
} from './types';

export const coverageQueryKey = ['market-data', 'coverage'] as const;
export const runsQueryKey = ['market-data', 'runs'] as const;
export const calendarQueryRoot = ['market-data', 'calendar'] as const;
export const calendarQueryKey = (month: string) => [...calendarQueryRoot, month] as const;
export const catchupQueryKey = ['market-data', 'catchup'] as const;
export const collectionQueryKey = ['market-data', 'settings'] as const;

/**
 * Частота чтения состояния прогона.
 *
 * Сессия котировок занимает 1–2 секунды, сессия позиций — около 2,5 минут:
 * чаще опрашивать бессмысленно, реже — рвано для быстрых прогонов. Это
 * свойство интерфейса, а не пользовательская настройка; настройка интервала
 * относится к портфелю (research.md R5).
 */
export const CATCHUP_POLL_MS = 3000;

export function fetchCoverage(): Promise<CoverageDto> {
  return apiGet<CoverageDto>('/api/market-data/coverage');
}

export function fetchCatchupState(): Promise<CatchupStateDto> {
  return apiGet<CatchupStateDto>('/api/market-data/catchup');
}

export function fetchRuns(): Promise<RunsDto> {
  return apiGet<RunsDto>('/api/market-data/runs');
}

export function fetchCalendar(month: string): Promise<CalendarMonthDto> {
  return apiGet<CalendarMonthDto>(`/api/market-data/calendar?month=${month}`);
}

export function startCatchup(request: LaunchRequest): Promise<CatchupStartResultDto> {
  const payload: Record<string, unknown> = {};
  if (request.groups !== null) payload.groups = request.groups;
  if (request.date_from !== null) payload.date_from = request.date_from;
  if (request.date_till !== null) payload.date_till = request.date_till;
  if (request.resume === true) payload.resume = true;

  return apiPost<CatchupStartResultDto>('/api/market-data/catchup', payload);
}

export function stopCatchup(): Promise<{ status: string; current: string | null }> {
  return apiDelete<{ status: string; current: string | null }>('/api/market-data/catchup');
}

export function fetchCollectionSettings(): Promise<CollectionSettingsDto> {
  return apiGet<CollectionSettingsDto>('/api/market-data/settings');
}

export function setCollectionPaused(paused: boolean): Promise<CollectionSettingsDto> {
  return apiPut<CollectionSettingsDto>('/api/market-data/settings', { paused });
}

/**
 * Идёт ли автоматический сбор.
 *
 * Состояние живёт в процессе сборщика и перезапуск его снимает, поэтому оно
 * читается с сервера, а не запоминается экраном: после перезапуска сбор снова
 * идёт, и показывать «остановлено» по факту прошлого нажатия было бы неправдой
 * (FR-029f).
 */
export function useCollectionSettings(): UseQueryResult<CollectionSettingsDto> {
  return useQuery({ queryKey: collectionQueryKey, queryFn: fetchCollectionSettings });
}

export function useSetCollectionPaused() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: setCollectionPaused,
    onSuccess: () => client.invalidateQueries({ queryKey: collectionQueryKey }),
  });
}

export function useCoverage(): UseQueryResult<CoverageDto> {
  return useQuery({ queryKey: coverageQueryKey, queryFn: fetchCoverage });
}

/**
 * Журнал последних прогонов.
 *
 * Отвечает на вопрос «как прошёл сбор» тогда, когда ход работы уже не показать:
 * состояние прогона живёт в памяти сборщика и исчезает с перезапуском, а журнал
 * лежит в хранилище (FR-005).
 */
export function useRuns(): UseQueryResult<RunsDto> {
  return useQuery({ queryKey: runsQueryKey, queryFn: fetchRuns });
}

/** Календарь сессий на месяц. Факт — слева от сегодня, справа ничего. */
export function useCalendar(month: string): UseQueryResult<CalendarMonthDto> {
  return useQuery({
    queryKey: calendarQueryKey(month),
    queryFn: () => fetchCalendar(month),
    // Прошлый месяц остаётся на экране, пока грузится следующий: иначе сетка
    // на мгновение пустеет, страница теряет высоту и прыгает под курсором.
    placeholderData: (previous) => previous,
  });
}

/**
 * Состояние прогона.
 *
 * Опрос идёт, пока прогон активен, и прекращается, когда он неактивен: при
 * `idle`, `stopped`, `finished` и `failed` новых чисел не появится, пока
 * человек не запустит сбор заново.
 *
 * Владелец этого запроса — оболочка приложения, а не страница: баннер
 * процесса виден в обоих разделах и не должен гаснуть при переходе в
 * портфель (FR-006, FR-035).
 */
export function useCatchupState(): UseQueryResult<CatchupStateDto> {
  return useQuery({
    queryKey: catchupQueryKey,
    queryFn: fetchCatchupState,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status !== undefined && isCatchupActive(status) ? CATCHUP_POLL_MS : false;
    },
  });
}

export function useStartCatchup() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: startCatchup,
    // Состояние прогона после запуска перечитывается с сервера: показывать
    // «идёт» по факту нажатия нельзя (FR-032).
    onSuccess: () => client.invalidateQueries({ queryKey: catchupQueryKey }),
  });
}

export function useStopCatchup() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: stopCatchup,
    onSuccess: () => client.invalidateQueries({ queryKey: catchupQueryKey }),
  });
}
