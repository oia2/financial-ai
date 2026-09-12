import '@testing-library/jest-dom/vitest';

import { afterAll, afterEach, beforeAll } from 'vitest';

import { installDialogPolyfill } from './dialog-polyfill';
import { server } from './msw/server';

// Приложение ходит по относительным путям, как в браузере: обработчики msw
// объявлены с префиксом '*', чтобы совпадать с ними.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// jsdom не реализует нативный `<dialog>`; панели раздела рыночных данных
// перенесены из артефакта именно как `dialog`.
installDialogPolyfill();
