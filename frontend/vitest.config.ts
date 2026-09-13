import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    // Часовой пояс прогона закреплён. Моменты теперь показываются в поясе
    // зрителя (FR-073), и без этой отметки ожидаемое время зависело бы от
    // машины: на UTC+7 тот же момент даёт другие часы, и тест падал бы у одного
    // разработчика и проходил у другого. UTC выбран намеренно НЕ московским —
    // так проверка подтверждает сам перевод, а не совпадение с поясом биржи.
    env: { TZ: 'UTC' },
  },
});
