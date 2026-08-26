import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { portfolioQueryKey, type RefreshIntervalDto } from '@/entities/portfolio';
import { apiGet, ApiError, apiPut } from '@/shared/api/client';

export const refreshIntervalQueryKey = ['refresh-interval'] as const;

const SETTINGS_URL = '/api/settings/refresh-interval';

/**
 * Настройка частоты фонового обновления (FR-031, FR-035).
 *
 * Разметка повторяет форму из утверждённого дизайна: поле с единицей
 * измерения, кнопка сохранения, подсказка с диапазоном, сообщение об ошибке
 * и строка действующего значения. Проверку выполняет приложение
 * (`noValidate`), чтобы показать формулировку из дизайна, а не браузерную.
 */
export function RefreshIntervalForm() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const initialised = useRef(false);

  const settings = useQuery({
    queryKey: refreshIntervalQueryKey,
    queryFn: () => apiGet<RefreshIntervalDto>(SETTINGS_URL),
  });

  useEffect(() => {
    if (settings.data && !initialised.current) {
      initialised.current = true;
      setDraft(String(settings.data.interval_seconds));
    }
  }, [settings.data]);

  const mutation = useMutation({
    mutationFn: (value: number) =>
      apiPut<RefreshIntervalDto>(SETTINGS_URL, { interval_seconds: value }),
    onSuccess: (data) => {
      setError(null);
      queryClient.setQueryData(refreshIntervalQueryKey, data);
      void queryClient.invalidateQueries({ queryKey: portfolioQueryKey });
    },
    onError: (cause) => setError(describeError(cause, settings.data)),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();

    if (!/^\d+$/.test(draft.trim())) {
      setError(rangeMessage(settings.data, 'Введите целое число секунд.'));
      return;
    }

    mutation.mutate(Number(draft.trim()));
  }

  if (!settings.data) return null;

  const { interval_seconds, min_seconds, max_seconds } = settings.data;

  return (
    <form className="interval-form" onSubmit={handleSubmit} noValidate>
      <label htmlFor="refresh-interval-input">Интервал автообновления</label>

      <div className="interval-control-row">
        <div className="interval-input-wrap">
          <input
            className="interval-input"
            id="refresh-interval-input"
            name="refreshInterval"
            type="number"
            inputMode="numeric"
            min={min_seconds}
            max={max_seconds}
            step={1}
            value={draft}
            aria-describedby="interval-hint interval-error"
            aria-invalid={error !== null}
            onChange={(event) => {
              setDraft(event.target.value);
              setError(null);
            }}
          />
          <span className="interval-unit">сек.</span>
        </div>
        <button
          className="primary-button interval-save"
          type="submit"
          disabled={mutation.isPending}
        >
          Сохранить
        </button>
      </div>

      <p className="field-hint" id="interval-hint">
        Целое число от {min_seconds} до {max_seconds} секунд.
      </p>

      <p className="field-error" id="interval-error" role="alert" hidden={error === null}>
        {error}
      </p>

      <p className="interval-current">
        Действует сейчас: <strong>{interval_seconds} с</strong>
      </p>
    </form>
  );
}

function rangeMessage(settings: RefreshIntervalDto | undefined, prefix: string): string {
  if (settings === undefined) return prefix;
  return (
    `${prefix} Допустимо от ${settings.min_seconds} до ${settings.max_seconds}. ` +
    `Прежний интервал ${settings.interval_seconds} с продолжает действовать.`
  );
}

function describeError(cause: unknown, settings: RefreshIntervalDto | undefined): string {
  if (cause instanceof ApiError && cause.code === 'interval_out_of_range') {
    return rangeMessage(settings, 'Значение вне допустимого диапазона.');
  }
  return 'Не удалось сохранить интервал. Попробуйте ещё раз.';
}
