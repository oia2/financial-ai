/**
 * Типы раздела «План портфеля».
 *
 * Форма задана `specs/007-daily-ml-lifecycle/contracts/portfolio-plan-api.md`.
 *
 * Два правила видны прямо в типах:
 *
 *  - **все суммы и количества — строки.** `float` на пути «БД → API → JSON»
 *    теряет копейки, и разбирать их в `number` ради удобства нельзя;
 *  - **цена всегда приходит с датой сессии.** Это закрытие названной сессии, а
 *    не текущая котировка, и поля без даты у неё нет намеренно.
 */

export interface PolicyDto {
  id: string;
  title: string;
  asset_count: number;
  description: string;
  /** Раздел исследования, откуда взяты веса. */
  source: string;
}

export interface PoliciesDto {
  policies: PolicyDto[];
  default_policy: string;
}

export interface PlanPositionDto {
  asset_id: string;
  ticker: string;
  name: string | null;
  rank: number;
  target_weight: string;
  /** Доля позиции от всего счёта после плана. Считает сервер. */
  weight_after: string;
  price: string;
  lot_size: number;
  target_lots: number;
  target_quantity: string;
  target_value: string;
  current_quantity: string;
  delta_quantity: string;
  delta_value: string;
}

export interface UntouchedDto {
  ticker: string | null;
  name: string | null;
  quantity: string;
  value: string;
  weight_after: string;
  reason: string;
}

export interface ExcludedDto {
  asset_id: string;
  rank: number;
  reason: string;
}

export interface PlanDto {
  asof_date: string;
  run_id: number;
  policy: string;
  /** Название правила: `equal_top20` — ключ для машины, не подпись для человека. */
  policy_title: string;
  /** Ранжирование получено эмулятором: скоры вымышлены. */
  emulated: boolean | null;
  price_source: { kind: string; session_date: string };
  capital: {
    eligible: string;
    limit: string;
    allocated: string;
    cash_after: string;
    fee_total: string;
    account_total: string;
  };
  positions: PlanPositionDto[];
  untouched: UntouchedDto[];
  excluded: ExcludedDto[];
}

export interface PlanRequest {
  policy: string;
  capital_limit?: string;
  fee_percent?: string;
}

/** Что делать с позицией: следует из знака разницы, а не приходит отдельно. */
export type PlanAction = 'buy' | 'sell' | 'keep';

export function actionOf(position: PlanPositionDto): PlanAction {
  const delta = Number(position.delta_quantity);
  if (delta > 0) return 'buy';
  if (delta < 0) return 'sell';
  return 'keep';
}
