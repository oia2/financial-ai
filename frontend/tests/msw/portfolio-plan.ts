/**
 * Ответы раздела «План портфеля» для тестов.
 *
 * Форма взята из `specs/007-daily-ml-lifecycle/contracts/portfolio-plan-api.md`.
 * Числа те же, что в тестах арифметики на сервере: капитал 100 000 ₽, равные
 * доли, SBER лотом по 10 штук.
 */

import type { PlanDto, PoliciesDto } from '@/entities/portfolio-plan';

export function policiesFixture(): PoliciesDto {
  return {
    policies: [
      {
        id: 'equal_top20',
        title: 'Равные доли · первые 20',
        asset_count: 20,
        description: 'Первые 20 активов ранжирования, равными долями расчётного капитала.',
        source: 'handoff §10.1',
      },
      {
        id: 'rank_zones_100',
        title: 'По диапазонам рангов · первые 100',
        asset_count: 100,
        description: 'Ранги 1–20: 24,03%; 21–30: 10,63%; 31–50: 20,39%; 51–100: 44,95%.',
        source: 'handoff §10.3',
      },
    ],
    default_policy: 'equal_top20',
  };
}

export function planFixture(overrides: Partial<PlanDto> = {}): PlanDto {
  return {
    asof_date: '2026-09-11',
    run_id: 143,
    policy: 'equal_top20',
    policy_title: 'Равные доли · первые 20',
    emulated: true,
    price_source: { kind: 'session_close', session_date: '2026-09-11' },
    capital: {
      eligible: '100000.000000000',
      limit: '100000.000000000',
      allocated: '96900.000000000',
      cash_after: '3100.000000000',
      fee_total: '38.76',
      account_total: '200000.000000000',
    },
    positions: [
      {
        asset_id: 'EQ_AST_SBER',
        ticker: 'SBER',
        name: 'Сбербанк',
        rank: 1,
        target_weight: '0.05',
        weight_after: '0.015',
        price: '300.00',
        lot_size: 10,
        target_lots: 1,
        target_quantity: '10',
        target_value: '3000.000000000',
        current_quantity: '100',
        delta_quantity: '-90',
        delta_value: '-27000.000000000',
      },
      {
        asset_id: 'EQ_AST_LKOH',
        ticker: 'LKOH',
        name: 'Лукойл',
        rank: 2,
        target_weight: '0.05',
        weight_after: '0.025',
        price: '2500.00',
        lot_size: 1,
        target_lots: 2,
        target_quantity: '2',
        target_value: '5000.000000000',
        current_quantity: '0',
        delta_quantity: '2',
        delta_value: '5000.000000000',
      },
    ],
    untouched: [
      {
        ticker: 'SU26238RMFS4',
        name: 'ОФЗ 26238',
        quantity: '200',
        value: '100000.000000000',
        weight_after: '0.5',
        reason: 'вне вселенной модели',
      },
    ],
    excluded: [{ asset_id: 'EQ_AST_XXXX', rank: 17, reason: 'не известен размер лота' }],
    ...overrides,
  };
}
