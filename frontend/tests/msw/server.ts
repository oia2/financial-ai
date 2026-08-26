import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { portfolioFixture } from './fixtures';

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
);

export { http, HttpResponse };
