/**
 * Параметры расчёта плана: правило, лимит распределения, комиссия.
 *
 * Правило выбирается из перечня сервера: произвольные веса человеком не
 * вводятся — иначе в плане появились бы доли, не проверенные исследованием
 * (FR-057).
 *
 * Ввод проверяется здесь же, до отправки: «не число» и «вне диапазона» —
 * поправимые опечатки, и обращаться ради них к серверу незачем. Отказы самого
 * расчёта приходят с сервера и подменяться этими сообщениями не должны.
 */

import { useEffect, useRef, useState } from 'react';

import type { PoliciesDto } from '@/entities/portfolio-plan';

export interface PlanSettings {
  policy: string;
  /** Пустая строка — лимита нет, распределяется весь расчётный капитал. */
  capitalLimit: string;
  feePercent: string;
}

const NUMBER = /^\d+([.,]\d+)?$/;

export function validate(settings: PlanSettings): string | null {
  if (settings.capitalLimit !== '' && !NUMBER.test(settings.capitalLimit)) {
    return 'Лимит распределения — число в рублях, например 148397,00.';
  }
  if (!NUMBER.test(settings.feePercent)) {
    return 'Комиссия — число в процентах, например 0,04.';
  }
  if (Number(settings.feePercent.replace(',', '.')) > 5) {
    return 'Комиссия за одну сторону выше 5% выглядит опечаткой.';
  }
  return null;
}

/** Ввод в форму запроса: запятая — десятичный разделитель, сервер ждёт точку. */
export function toRequest(settings: PlanSettings) {
  return {
    policy: settings.policy,
    capital_limit:
      settings.capitalLimit === '' ? undefined : settings.capitalLimit.replace(',', '.'),
    fee_percent: settings.feePercent.replace(',', '.'),
  };
}

export function PlanSettingsDialog({
  open,
  policies,
  settings,
  onApply,
  onClose,
}: {
  open: boolean;
  policies: PoliciesDto | undefined;
  settings: PlanSettings;
  onApply: (settings: PlanSettings) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState(settings);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;

    if (open && !dialog.open) {
      setDraft(settings);
      setError(null);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open, settings]);

  const selected = policies?.policies.find((policy) => policy.id === draft.policy);

  function submit(event: React.FormEvent) {
    event.preventDefault();

    const problem = validate(draft);
    setError(problem);
    if (problem === null) onApply(draft);
  }

  return (
    <dialog
      className="pp-settings-dialog"
      ref={ref}
      onClose={onClose}
      aria-labelledby="ppSettingsTitle"
      data-od-id="position-plan-settings-dialog"
    >
      <div className="pp-dialog-head">
        <h2 id="ppSettingsTitle">Параметры расчёта</h2>
        <button
          className="icon-button"
          type="button"
          aria-label="Закрыть параметры"
          data-od-id="close-position-plan-settings"
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <form onSubmit={submit} noValidate>
        <div className="pp-settings-body">
          <div className="pp-field">
            <label htmlFor="ppPolicy">Правило распределения</label>
            <select
              id="ppPolicy"
              aria-describedby="ppPolicyHint"
              data-od-id="position-plan-policy"
              value={draft.policy}
              onChange={(event) => setDraft({ ...draft, policy: event.target.value })}
            >
              {(policies?.policies ?? []).map((policy) => (
                <option key={policy.id} value={policy.id}>
                  {policy.title}
                </option>
              ))}
            </select>
            <p id="ppPolicyHint">
              {selected ? `${selected.description} Источник: ${selected.source}.` : ''}
            </p>
          </div>

          <div className="pp-field">
            <label htmlFor="ppBudget">Лимит распределения, ₽</label>
            <div className="pp-input-row">
              <input
                id="ppBudget"
                inputMode="decimal"
                type="text"
                aria-describedby="ppBudgetHint"
                data-od-id="position-plan-budget"
                value={draft.capitalLimit}
                onChange={(event) => setDraft({ ...draft, capitalLimit: event.target.value })}
              />
              <button
                className="secondary-button"
                type="button"
                data-od-id="position-plan-all-capital"
                onClick={() => setDraft({ ...draft, capitalLimit: '' })}
              >
                Вся сумма
              </button>
            </div>
            <p id="ppBudgetHint">
              Лимит складывается из текущих акций и денег, а не из суммы покупок. Всё, что не
              распределится, останется деньгами. Облигации и денежный фонд сохраняются.
            </p>
          </div>

          <div className="pp-field">
            <label htmlFor="ppFee">Комиссия за одну сторону, %</label>
            <input
              id="ppFee"
              inputMode="decimal"
              type="text"
              aria-describedby="ppFeeHint"
              data-od-id="position-plan-fee"
              value={draft.feePercent}
              onChange={(event) => setDraft({ ...draft, feePercent: event.target.value })}
            />
            <p id="ppFeeHint">
              Применяется к каждой стороне — и к покупке, и к продаже. Из лимита не резервируется:
              оборот известен только после распределения, и резерв «на глаз» сместил бы веса.
            </p>
          </div>

          <div className="pp-method-note">
            <strong>Правило задаёт веса, ранжирование — состав.</strong>
            <p>
              Доступные правила взяты из исследования Daily ML. Смена правила к модели не
              обращается: состав определяет порядок последнего успешного ранжирования.
            </p>
          </div>

          {error && (
            <p className="pp-settings-error" role="alert">
              {error}
            </p>
          )}
        </div>

        <div className="pp-settings-actions">
          <button
            className="secondary-button"
            type="button"
            data-od-id="cancel-position-plan-settings"
            onClick={onClose}
          >
            Отмена
          </button>
          <button className="primary-button" type="submit" data-od-id="recalculate-position-plan">
            Пересчитать план
          </button>
        </div>
      </form>
    </dialog>
  );
}
