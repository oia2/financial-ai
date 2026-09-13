/**
 * Управление ранжированием: пауза, возобновление, проверка, повтор.
 *
 * Четыре действия, и больше в разделе не требуется (FR-051). Смысл каждого
 * задан ответом сервера, а не нажатием:
 *
 *  - **«Проверить сейчас» — это проверка, а не пересчёт.** Уже успешно
 *    обработанный вход пропускается, и сообщение обязано так и звучать: иначе
 *    кнопка читалась бы как «посчитать заново», чем она не является;
 *  - **стоп не останавливает сбор данных.** Это разные механизмы, и слитое
 *    прочтение дороже всех прочих ошибок на этом экране (FR-055);
 *  - **повтор возможен не всегда**: удалённый набор и исчерпанные попытки —
 *    разные причины, и каждая доходит своей (FR-039, FR-040).
 */

import { useCallback, useState } from 'react';

import { useReconcile, useRetryRun, useSetPaused } from '@/entities/daily-ml';
import { ApiError, ServerUnreachableError } from '@/shared/api/client';

const REFUSALS: Record<string, string> = {
  run_not_found: 'Прогон не найден. Возможно, он уже удалён.',
  run_not_failed: 'Повторять можно только отказавший прогон.',
  dataset_expired: 'Входной набор удалён по сроку хранения: повторять нечем.',
  attempts_exhausted: 'Попытки по этому прогону исчерпаны.',
  worker_unavailable: 'Сборщик данных недоступен. Команда не выполнена.',
};

export function refusalMessage(error: unknown): string {
  if (error instanceof ServerUnreachableError) return 'Нет связи с сервером Financial AI.';
  if (error instanceof ApiError && error.code !== undefined) {
    return REFUSALS[error.code] ?? 'Команда отклонена.';
  }
  return 'Команда отклонена.';
}

export function useDailyMlControl() {
  const reconcile = useReconcile();
  const setPaused = useSetPaused();
  const retry = useRetryRun();

  /** Ответ на последнее действие. Живёт в интерфейсе и только в нём. */
  const [notice, setNotice] = useState<string | null>(null);

  const checkNow = useCallback(() => {
    reconcile.mutate(undefined, {
      onSuccess: (result) => {
        if (result.already_up_to_date) {
          // Ровно то, что произошло: работы нет. «Пересчитано» здесь было бы
          // неправдой — успешный вход не пересчитывается никогда.
          setNotice('Новых готовых данных нет. Уже посчитанное не пересчитывается.');
          return;
        }
        setNotice(
          result.queued > 0
            ? 'Появились готовые данные: ранжирование принято к обработке.'
            : 'Проверка выполнена. Заданий не создано.',
        );
      },
      onError: (error) => setNotice(refusalMessage(error)),
    });
  }, [reconcile]);

  const togglePause = useCallback(
    (paused: boolean) => {
      setPaused.mutate(paused, {
        onSuccess: () =>
          setNotice(
            paused
              ? 'Автоматическое ранжирование остановлено. Сбор рыночных данных продолжается.'
              : 'Автоматическое ранжирование запущено.',
          ),
        onError: (error) => setNotice(refusalMessage(error)),
      });
    },
    [setPaused],
  );

  const retryRun = useCallback(
    (id: number) => {
      retry.mutate(id, {
        onSuccess: () => setNotice('Прогон принят к повторной обработке.'),
        onError: (error) => setNotice(refusalMessage(error)),
      });
    },
    [retry],
  );

  return { notice, checkNow, togglePause, retryRun };
}
