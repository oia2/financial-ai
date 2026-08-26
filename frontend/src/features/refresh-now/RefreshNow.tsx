import { useMutation, useQueryClient } from '@tanstack/react-query';

import { portfolioQueryKey, type RefreshResultDto } from '@/entities/portfolio';
import { apiPost, ServerUnreachableError } from '@/shared/api/client';
import { useToast } from '@/shared/ui/toast/ToastHost';

import './refresh-now.css';

/**
 * Ручное обновление (US3).
 *
 * Повторное нажатие во время выполнения не создаёт второго запроса: кнопка
 * заблокирована, а на стороне сервера действует общий лок (FR-029).
 */
export function RefreshNow({ inProgress = false }: { inProgress?: boolean }) {
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

  const busy = mutation.isPending || inProgress;

  return (
    <button
      type="button"
      className="refresh-now"
      onClick={() => mutation.mutate()}
      disabled={busy}
      aria-busy={busy}
    >
      {busy ? 'Обновляем…' : 'Обновить сейчас'}
    </button>
  );
}
