import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { portfolioFixture } from './fixtures';
import { catchupFixture, coverageFixture } from './market-data';

export const server = setupServer(
  http.get('*/api/portfolio', () => HttpResponse.json(portfolioFixture())),
  http.get('*/api/settings/refresh-interval', () =>
    HttpResponse.json({
      interval_seconds: 60,
      min_seconds: 15,
      max_seconds: 3600,
      default_seconds: 60,
    }),
  ),
  http.get('*/api/market-data/coverage', () => HttpResponse.json(coverageFixture())),
  http.get('*/api/market-data/catchup', () => HttpResponse.json(catchupFixture('idle'))),
);

export { http, HttpResponse };
