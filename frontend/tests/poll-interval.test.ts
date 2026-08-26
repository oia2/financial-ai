/**
 * Частота опроса API (T047).
 *
 * Worker ходит к брокеру раз в настроенный интервал; интерфейс читает уже
 * сохранённое состояние в десять раз чаще, поэтому отображаемые данные
 * отстают не более чем на 10% интервала (SC-010, research §6).
 */

import { describe, expect, it } from 'vitest';

import { MAX_POLL_SECONDS, MIN_POLL_SECONDS, pollIntervalSeconds } from '@/entities/portfolio';

describe('poll-интервал', () => {
  it('составляет десятую часть интервала синхронизации', () => {
    expect(pollIntervalSeconds(60)).toBe(6);
    expect(pollIntervalSeconds(120)).toBe(12);
    expect(pollIntervalSeconds(300)).toBe(30);
  });

  it('не опускается ниже нижней границы', () => {
    // При минимальном интервале 15 c десятая часть — 1,5 c: слишком часто.
    expect(pollIntervalSeconds(15)).toBe(MIN_POLL_SECONDS);
    expect(pollIntervalSeconds(30)).toBe(MIN_POLL_SECONDS);
  });

  it('не поднимается выше верхней границы', () => {
    expect(pollIntervalSeconds(3600)).toBe(MAX_POLL_SECONDS);
    expect(pollIntervalSeconds(600)).toBe(MAX_POLL_SECONDS);
  });

  it('держит отставание в пределах 10% интервала на рабочем диапазоне', () => {
    for (const interval of [60, 120, 180, 300]) {
      expect(pollIntervalSeconds(interval)).toBeLessThanOrEqual(interval * 0.1);
    }
  });
});
