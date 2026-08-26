import '@testing-library/jest-dom/vitest';

import { afterAll, afterEach, beforeAll } from 'vitest';

import { server } from './msw/server';

// Приложение ходит по относительным путям, как в браузере: обработчики msw
// объявлены с префиксом '*', чтобы совпадать с ними.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
