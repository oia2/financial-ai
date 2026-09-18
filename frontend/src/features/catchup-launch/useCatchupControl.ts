/**
 * Управление догоном: запуск, остановка, продолжение.
 *
 * Хук держит то, что принадлежит интерфейсу и только ему: введённый диапазон
 * (чтобы показать его рядом с принятым при сужении) и сообщение отказа.
 * Состояние самого прогона живёт на сервере и читается оттуда — локальное
 * представление источником истины не является (FR-036).
 */

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import {
  catchupQueryKey,
  useStartCatchup,
  useStopCatchup,
  type CatchupStartResultDto,
  type GroupId,
  type LaunchRequest,
} from '@/entities/market-data';
import { ApiError, ServerUnreachableError } from '@/shared/api/client';
import { useToast } from '@/shared/ui/toast/ToastHost';

export interface ClampContext {
  requestedFrom: string | null;
  requestedTill: string | null;
  acceptedFrom: string | null;
  acceptedTill: string | null;
}

/**
 * Сообщения отказа.
 *
 * Каждая причина своя: подмена одной другой приводит к неверным действиям
 * ценой сотен обращений к бирже (FR-038). Текст ответа сервера в интерфейс не
 * переносится — он технический (FR-043).
 */
const REFUSALS: Record<string, string> = {
  catchup_already_running: 'Догон уже выполняется. Текущий прогон продолжается без изменений.',
  backfill_required:
    'В хранилище нет наблюдений: нужна первичная загрузка. Обратитесь к администратору системы.',
  unknown_group: 'Неизвестная группа. Запуск отклонён. Выберите группы из списка.',
  invalid_group: 'Неизвестная группа. Запуск отклонён. Выберите группы из списка.',
  invalid_range: 'Начало диапазона должно быть не позже конца.',
  invalid_date: 'Не удалось прочитать дату. Формат: ДД.ММ.ГГГГ.',
  worker_unavailable: 'Сборщик данных недоступен. Команда не выполнена.',
};

export function refusalMessage(error: unknown): string {
  if (error instanceof ServerUnreachableError) return 'Нет связи с сервером Financial AI.';
  if (error instanceof ApiError && error.code !== undefined) {
    return REFUSALS[error.code] ?? 'Запуск отклонён.';
  }
  return 'Запуск отклонён.';
}

/** Сообщение поверх состояния прогона: заголовок и пояснение, как в артефакте. */
export interface PageNotice {
  title: string;
  message: string;
  error?: boolean;
}

export function useCatchupControl() {
  const start = useStartCatchup();
  const stop = useStopCatchup();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [clamp, setClamp] = useState<ClampContext | null>(null);
  /** Отказ, показанный поверх состояния прогона: счётчики он не затирает. */
  const [pageNotice, setPageNotice] = useState<PageNotice | null>(null);
  /**
   * Сервер ответил, что догонять нечего.
   *
   * Пока это так, новый прогон не предлагается (FR-038): кнопка запуска
   * пропадает, как и в артефакте. Признак снимается при перечитывании сводки —
   * иначе человек, у которого пропуски появились, остался бы без выхода из
   * этого состояния до перезагрузки страницы.
   */
  const [nothingToCatchUp, setNothingToCatchUp] = useState(false);

  /**
   * Снять признак «догонять нечего» — при перечитывании сводки.
   *
   * Ссылка стабильна намеренно: этот обработчик вызывается из эффекта
   * страницы, и новая функция на каждый рендер заставляла бы эффект
   * срабатывать постоянно — уведомление стиралось бы сразу после появления.
   */
  const clearNothingToCatchUp = useCallback(() => {
    setNothingToCatchUp(false);
    setPageNotice(null);
  }, []);

  function launch(request: LaunchRequest, resumed = false) {
    setRefusal(null);

    start.mutate(request, {
      onSuccess: (result: CatchupStartResultDto) => {
        setDrawerOpen(false);

        if (result.status === 'idle' && result.requested_sessions === 0) {
          setNothingToCatchUp(true);
          setPageNotice({
            title: 'Пропущенных сессий нет',
            message: 'В выбранном диапазоне догонять нечего. Новый прогон не запущен.',
          });
          toast.show('Догонять нечего');
          return;
        }

        setNothingToCatchUp(false);
        setPageNotice(null);
        setClamp(
          result.clamped === true && !resumed
            ? {
                requestedFrom: request.date_from,
                requestedTill: request.date_till,
                acceptedFrom: result.date_from ?? null,
                acceptedTill: result.date_till ?? null,
              }
            : null,
        );

        // Состояние перечитывается СРАЗУ. У остановленного прогона опрос
        // выключен — само оно не изменится, — и панель показывала бы остановку
        // с предложением продолжить уже после того, как продолжение началось
        // (FR-059).
        void queryClient.invalidateQueries({ queryKey: catchupQueryKey });

        if (resumed) {
          toast.show(
            result.resumed === false
              ? 'Остановленный прогон не сохранился: запущен обычный догон.'
              : 'Продолжение запущено: только непройденные сессии.',
          );
          return;
        }

        toast.show(
          result.clamped === true
            ? 'Догон запущен. Диапазон ограничен окном.'
            : 'Догон запущен. Можно перейти в другой раздел.',
        );
      },
      onError: (error) => {
        const message = refusalMessage(error);

        // Отказ повторного запуска не затирает состояние идущего прогона: он
        // показывается поверх, а счётчики остаются на экране (FR-039). Раз
        // сервер сообщает, что прогон идёт, состояние перечитывается — на
        // экране могло быть устаревшее «не запущен».
        if (error instanceof ApiError && error.code === 'catchup_already_running') {
          setDrawerOpen(false);
          setPageNotice({ title: 'Повторный запуск отклонён', message });
          void queryClient.invalidateQueries({ queryKey: catchupQueryKey });
          return;
        }

        // Продолжение идёт без формы, поэтому его отказ показывать негде,
        // кроме страницы.
        if (resumed) {
          setPageNotice({ title: 'Продолжение не выполнено', message, error: true });
          return;
        }

        setRefusal(message);
      },
    });
  }

  /**
   * Продолжить догон.
   *
   * Немедленный запуск с группами прошлого прогона, без формы — как в
   * артефакте. Диапазон не передаётся: его берёт сервер — по НЕПРОЙДЕННЫМ
   * сессиям остановленного прогона. Прежде продолжение считало пропуски заново
   * по всему окну, и счётчик сессий менялся скачком: кнопка обещала
   * продолжение, а делала новый прогон (FR-025, FR-058).
   */
  function resume(groups: GroupId[]) {
    launch(
      {
        groups: groups.length > 0 ? groups : null,
        date_from: null,
        date_till: null,
        resume: true,
      },
      true,
    );
  }

  function requestStop() {
    // Остановка выполняется одним действием: запрос безопасно доводит
    // текущую сессию до конца, подтверждать нечего (FR-023).
    stop.mutate(undefined, {
      onSuccess: () => toast.show('Остановка запрошена. Текущая сессия будет завершена.'),
      onError: (error) => toast.show(refusalMessage(error)),
    });
  }

  return {
    drawerOpen,
    openDrawer: () => {
      setRefusal(null);
      setDrawerOpen(true);
    },
    closeDrawer: () => setDrawerOpen(false),
    launch: (request: LaunchRequest) => launch(request),
    resume,
    launching: start.isPending,
    refusal,
    requestStop,
    clamp,
    pageNotice,
    nothingToCatchUp,
    clearNothingToCatchUp,
  };
}
