/**
 * Раздел «План портфеля».
 *
 * Ранжирование и текущий портфель → список покупок, продаж и позиций без
 * изменения. Композиция перенесена из артефакта Open Design
 * (`portfolio-plan.html`).
 *
 * **Расчёт портфель не меняет, заявок не выставляет и совершить сделку не
 * предлагает** (FR-063). Действий исполнения в разделе нет — ни кнопки, ни
 * ссылки, ни «отправить в терминал».
 *
 * Отказ показывается как отсутствие плана, а не как пустой состав: пустая
 * таблица прочиталась бы как «продай всё». Причина берётся из ответа сервера —
 * «нет ранжирования», «ранжирование устарело», «счёт не подключён» требуют
 * разного ожидания, и подменять одну другой нельзя (FR-061).
 */

import { useEffect, useState } from 'react';

import { navigate } from '@/app/router';
import {
  useCalculatePlan,
  usePolicies,
  type PlanAction,
  type PlanDto,
} from '@/entities/portfolio-plan';
import {
  PlanSettingsDialog,
  toRequest,
  type PlanSettings,
} from '@/features/plan-settings/PlanSettingsDialog';
import { ApiError, ServerUnreachableError } from '@/shared/api/client';
import { PositionPlan } from '@/widgets/position-plan/PositionPlan';

/** Почему плана нет. Каждая причина своя: они требуют разного ожидания. */
const UNAVAILABLE: Record<string, string> = {
  no_successful_ranking:
    'Успешного ранжирования ещё не было. План появится после первого завершённого прогона.',
  ranking_stale:
    'Вход последнего ранжирования изменился: решение относится к другим данным. Дождитесь нового прогона.',
  broker_not_connected: 'Счёт не подключён: деньги и позиции неизвестны.',
  portfolio_stale: 'Снимок счёта устарел. Обновите состояние портфеля и пересчитайте план.',
  no_prices: 'Цен закрытия нет: дождитесь сбора рыночных данных.',
  insufficient_assets: 'В ранжировании меньше активов, чем требует выбранное правило.',
};

function unavailableMessage(error: unknown): string {
  if (error instanceof ServerUnreachableError) return 'Нет связи с сервером Financial AI.';
  if (error instanceof ApiError && error.code !== undefined) {
    const base = UNAVAILABLE[error.code];
    if (error.code === 'insufficient_assets') {
      // Сервер называет требуемое и доступное число: без них человеку
      // непонятно, какое правило выбрать вместо этого. В `detail` лежит тело
      // ответа целиком, поэтому разбирается его вложенный `detail`.
      const body = error.detail as
        { detail?: { required?: number; available?: number } } | undefined;
      const detail = body?.detail;
      if (detail?.required !== undefined && detail.available !== undefined) {
        return `Правило требует ${detail.required} активов, а в ранжировании их ${detail.available}. Выберите правило поменьше.`;
      }
    }
    return base ?? error.message;
  }
  return 'Расчёт не выполнен.';
}

const DEFAULT_SETTINGS: PlanSettings = {
  policy: 'equal_top20',
  capitalLimit: '',
  feePercent: '0,04',
};

export function PortfolioPlanPage() {
  const policies = usePolicies();
  const calculate = useCalculatePlan();

  const [settings, setSettings] = useState<PlanSettings>(DEFAULT_SETTINGS);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [filter, setFilter] = useState<PlanAction | 'all'>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const defaultPolicy = policies.data?.default_policy;
  const calculateMutate = calculate.mutate;

  // Первый расчёт выполняется сам, по умолчанию сервера: заставлять человека
  // нажимать «Пересчитать», чтобы увидеть план, нечего — правило по умолчанию
  // уже выбрано.
  useEffect(() => {
    if (defaultPolicy === undefined) return;

    const initial = { ...DEFAULT_SETTINGS, policy: defaultPolicy };
    setSettings(initial);
    calculateMutate(toRequest(initial));
  }, [defaultPolicy, calculateMutate]);

  function apply(next: PlanSettings) {
    setSettings(next);
    setDialogOpen(false);
    setPage(1);
    calculate.mutate(toRequest(next));
  }

  const plan: PlanDto | undefined = calculate.data;

  return (
    <main className="ml-main pp-main" data-od-id="portfolio-plan-main">
      <section
        className="position-plan"
        data-od-id="position-plan"
        aria-labelledby="positionPlanTitle"
        tabIndex={-1}
      >
        <div className="pp-heading">
          <div>
            <p className="pp-kicker">На основе текущего портфеля</p>
            <h1 id="positionPlanTitle" data-od-id="position-plan-title">
              План портфеля
            </h1>
          </div>
          <div className="pp-heading-actions">
            <button
              className="secondary-button"
              type="button"
              data-od-id="position-plan-settings"
              onClick={() => setDialogOpen(true)}
            >
              Параметры расчёта
            </button>
          </div>
        </div>

        <p className="pp-intro">
          Ранжирование и текущий портфель → список покупок, продаж и позиций без изменения. Расчёт
          портфель не меняет и заявок не отправляет.
        </p>

        {calculate.isPending && <p role="status">Считаем план…</p>}

        {calculate.isError && (
          <div className="pp-unavailable" role="status" data-od-id="position-plan-unavailable">
            <h3>Расчёт пока недоступен</h3>
            <p>{unavailableMessage(calculate.error)}</p>
          </div>
        )}

        {plan !== undefined && !calculate.isPending && (
          <PositionPlan
            plan={plan}
            filter={filter}
            page={page}
            pageSize={pageSize}
            onFilterChange={(next) => {
              setFilter(next);
              setPage(1);
            }}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
            onOpenRun={() => navigate('daily-ml')}
          />
        )}
      </section>

      <PlanSettingsDialog
        open={dialogOpen}
        policies={policies.data}
        settings={settings}
        onApply={apply}
        onClose={() => setDialogOpen(false)}
      />
    </main>
  );
}
