import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { portfolioFixture } from './fixtures';

export const server = setupServer(
  http.get('*/api/portfolio', () => HttpResponse.json(portfolioFixture())),
);

export { http, HttpResponse };
