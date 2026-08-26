import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { portfolioQueryKey, type RefreshIntervalDto } from '@/entities/portfolio';
import { apiGet, ApiError, apiPut } from '@/shared/api/client';

import './refresh-interval-setting.css';

export const refreshIntervalQueryKey = ['refresh-interval'] as const;

const SETTINGS_URL = '/api/settings/refresh-interval';

/**
 * Настройка частоты фонового обновления (FR-031, FR-035).
 *
 * Границы диапазона приходят с сервера и показываются пользователю, а не
 * хардкодятся здесь. Недопустимое значение отклоняется, и прежний интервал
 * продолжает действовать (US2 AS4).
 */
export function RefreshIntervalSetting() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  // Поле заполняется действующим значением один раз: иначе очистка поля
  // пользователем тут же откатывалась бы обратно.
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
      // Новая частота влияет и на опрос портфеля.
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
    // noValidate: проверку выполняет приложение, чтобы пользователь видел
    // объяснение с диапазоном и с прежним значением (US2 AS4), а не
    // браузерную подсказку, которую нельзя сформулировать.
    <form className="interval-form" onSubmit={handleSubmit} noValidate>
      <label className="interval-label" htmlFor="refresh-interval-input">
        Интервал автообновления
      </label>

      <div className="interval-row">
        <input
          id="refresh-interval-input"
          className="interval-input numeric"
          type="number"
          inputMode="numeric"
          min={min_seconds}
          max={max_seconds}
          step={1}
          value={draft}
          aria-describedby="refresh-interval-hint refresh-interval-error"
          aria-invalid={error !== null}
          onChange={(event) => {
            setDraft(event.target.value);
            setError(null);
          }}
        />
        <span className="interval-unit muted">сек.</span>
        <button type="submit" className="interval-save" disabled={mutation.isPending}>
          Сохранить
        </button>
      </div>

      <p id="refresh-interval-hint" className="interval-hint muted">
        Целое число от {min_seconds} до {max_seconds} секунд. Действует сейчас:{' '}
        <strong className="numeric">{interval_seconds} с</strong>
      </p>

      {error === null ? null : (
        <p id="refresh-interval-error" className="interval-error" role="alert">
          {error}
        </p>
      )}
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
