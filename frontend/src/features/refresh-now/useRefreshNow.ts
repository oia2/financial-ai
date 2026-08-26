import { useMutation, useQueryClient } from '@tanstack/react-query';

import { portfolioQueryKey, type RefreshResultDto } from '@/entities/portfolio';
import { apiPost, ServerUnreachableError } from '@/shared/api/client';
import { useToast } from '@/shared/ui/toast/ToastHost';

/**
 * Ручное обновление (US3).
 *
 * Вынесено в хук, потому что в утверждённом дизайне это действие живёт сразу
 * в трёх местах: иконка в шапке, пункт меню счёта и действие в баннере.
 * Логика у них одна, включая защиту от повторного запуска (FR-029).
 */
export function useRefreshNow() {
  const queryClient = useQueryClient();
  const toast = useToast();

  const mutation = useMutation({
    mutationFn: () => apiPost<RefreshResultDto>('/api/portfolio/refresh'),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: portfolioQueryKey });

      if (result.status === 'ok') {
        toast.show(result.deduplicated ? 'Обновление уже выполнялось' : 'Портфель обновлён');
      } else {
        toast.show('Не удалось обновить портфель');
      }
    },
    onError: (error) => {
      toast.show(
        error instanceof ServerUnreachableError
          ? 'Нет связи с сервером Financial AI'
          : 'Не удалось обновить портфель',
      );
    },
  });

  return {
    run: () => {
      if (!mutation.isPending) mutation.mutate();
    },
    isPending: mutation.isPending,
  };
}
