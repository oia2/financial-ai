import type { PortfolioDto } from '@/entities/portfolio';

/** Ответ API с одним успешно синхронизированным состоянием. */
export function portfolioFixture(overrides: Partial<PortfolioDto> = {}): PortfolioDto {
  return {
    broker: {
      status: 'connected',
      account: {
        display_name: 'Основной брокерский счёт',
        masked_id: '•• 4821',
        currency: 'RUB',
      },
    },
    snapshot: {
      captured_at: '2026-08-26T11:32:18Z',
      age_seconds: 12,
      total_value: '402609.000000000',
      cash: '40545.000000000',
      cash_share: '0.100705',
      unrealized_pnl: '4590.000000000',
      unrealized_pnl_percent: '0.0129',
      positions_count: 2,
      positions: [
        {
          instrument_uid: 'uid-sber',
          ticker: 'SBER',
          name: 'Сбербанк',
          asset_type: 'share',
          currency: 'RUB',
          quantity: '1200.000000000',
          average_price: '281.400000000',
          current_price: '301.720000000',
          accrued_interest: '0',
          value: '362064.000000000',
          unrealized_pnl: '24384.000000000',
          unrealized_pnl_percent: '0.0722',
          share: '0.899295',
        },
        {
          instrument_uid: 'uid-unknown',
          ticker: null,
          name: null,
          asset_type: null,
          currency: 'RUB',
          quantity: '5.000000000',
          average_price: null,
          current_price: '0.000000000',
          accrued_interest: '0',
          value: '0.000000000',
          unrealized_pnl: null,
          unrealized_pnl_percent: null,
          share: '0.000000000',
        },
      ],
    },
    sync: {
      status: 'ok',
      last_success_at: '2026-08-26T11:32:18Z',
      last_attempt_at: '2026-08-26T11:32:18Z',
      failure_reason_code: null,
      is_stale: false,
      stale_after_seconds: 180,
      refresh_interval_seconds: 60,
      in_progress: false,
    },
    ...overrides,
  };
}
