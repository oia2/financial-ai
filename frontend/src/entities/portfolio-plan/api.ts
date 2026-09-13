/**
 * Запросы раздела «План портфеля».
 *
 * Расчёт — `POST`, потому что он принимает настройки человека, а не потому что
 * что-то меняет: портфель он не меняет, заявок не выставляет и к брокеру не
 * обращается (FR-063). Результат не кэшируется — портфель меняется непрерывно,
 * и вчерашний ответ на сегодняшний вопрос был бы неправдой.
 */

import { useMutation, useQuery, type UseQueryResult } from '@tanstack/react-query';

import { apiGet, apiPost } from '@/shared/api/client';

import type { PlanDto, PlanRequest, PoliciesDto } from './types';

export const policiesQueryKey = ['portfolio-plan', 'policies'] as const;

export function usePolicies(): UseQueryResult<PoliciesDto> {
  return useQuery({
    queryKey: policiesQueryKey,
    queryFn: () => apiGet<PoliciesDto>('/api/portfolio-plan/policies'),
    // Перечень правил задаёт сервер, и меняется он только с выпуском.
    staleTime: Infinity,
  });
}

export function useCalculatePlan() {
  return useMutation({
    mutationFn: (request: PlanRequest) => apiPost<PlanDto>('/api/portfolio-plan', request),
  });
}
