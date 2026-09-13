import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import { historyFixture, runDetailFixture, runFixture, statusFixture } from './daily-ml';
import { portfolioFixture } from './fixtures';
import { planFixture, policiesFixture } from './portfolio-plan';
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
  // Оболочка читает состояние ранжирования в любом разделе: без этих ответов
  // упали бы все тесты, а не только тесты раздела.
  http.get('*/api/daily-ml/status', () => HttpResponse.json(statusFixture())),
  http.get('*/api/daily-ml/runs', () => HttpResponse.json(historyFixture([runFixture()]))),
  http.get('*/api/daily-ml/runs/:id', () => HttpResponse.json(runDetailFixture())),
  http.get('*/api/portfolio-plan/policies', () => HttpResponse.json(policiesFixture())),
  http.post('*/api/portfolio-plan', () => HttpResponse.json(planFixture())),
);

export { http, HttpResponse };
