/**
 * Календарь сессий и расписание сбора.
 *
 * Перенесено из артефакта Open Design `market-data.html` (spec 008).
 *
 * Главное правило: **слева от сегодняшнего дня — факт, справа — ожидание.**
 * Календарь строится по состоявшимся торгам опорной бумаги, а не по справочнику
 * праздников, поэтому будущих сессий он не знает и утверждать о них ничего не
 * может. Пунктир — не украшение, а признание незнания (FR-023).
 */

import { useState } from 'react';

import { useCalendar } from '@/entities/market-data';
import { formatIsoDate } from '@/shared/lib/market-format';

const MONTHS = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
];

const NOTE: Record<string, string> = {
  nontrade: 'торгов не было',
  open: 'не закрыта',
  future: 'ожидается',
};

function monthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function parseMonth(month: string): [number, number] {
  const [year = 2026, index = 1] = month.split('-').map(Number);
  return [year, index];
}

function shift(month: string, delta: number): string {
  const [year, index] = parseMonth(month);
  return monthKey(new Date(year, index - 1 + delta, 1));
}

export function CollectionCalendar({
  nextSession,
  threshold,
  lastClosed,
  paused,
}: {
  /** Сессия, которую возьмёт следующий сбор. Из торгового календаря (FR-024a). */
  nextSession: string | null;
  /** Порог сбора текущей сессии: время и биржевое время. */
  threshold: { local: string; exchange: string };
  lastClosed: string | null;
  paused: boolean;
}) {
  const [month, setMonth] = useState(() => monthKey(new Date()));
  const calendar = useCalendar(month);

  const days = calendar.data?.days ?? [];
  const [year, index] = parseMonth(month);
  const firstWeekday = (new Date(year, index - 1, 1).getDay() + 6) % 7;
  const forward = shift(monthKey(new Date()), 1);

  return (
    <section className="schedule-section" aria-labelledby="scheduleTitle">
      <div className="section-heading">
        <h2 id="scheduleTitle">Расписание и календарь</h2>
      </div>

      <ul className="schedule-facts">
        <li>
          <span>Следующий сбор</span>
          <strong>
            {paused
              ? 'не будет'
              : nextSession === null
                ? 'по расписанию'
                : formatIsoDate(nextSession)}
            <span className="msk">
              {paused ? 'пока автосбор на паузе' : `после ${threshold.local}`}
            </span>
          </strong>
        </li>
        <li>
          <span>Последняя закрытая</span>
          <strong className="mono">{formatIsoDate(lastClosed)}</strong>
        </li>
      </ul>

      <p className="schedule-explanation">
        Порог {threshold.exchange} — момент, с которого разрешено собирать сегодняшнюю сессию. Часть
        источников публикуется позже: позиции по фьючерсам приходят с задержкой и добираются
        повторами. Ручной сбор истории от порога не зависит.
      </p>

      <details className="calendar-details">
        <summary>
          Календарь сессий{' '}
          <span className="quiet">
            {MONTHS[index - 1]} {year}
          </span>
        </summary>

        <div className="calendar-content">
          <div>
            <div className="calendar-nav">
              <button
                className="icon-button"
                type="button"
                aria-label="Предыдущий месяц"
                onClick={() => setMonth(shift(month, -1))}
              >
                ‹
              </button>
              <strong>
                {MONTHS[index - 1]} {year}
              </strong>
              <button
                className="icon-button"
                type="button"
                aria-label="Следующий месяц"
                disabled={month >= forward}
                onClick={() => setMonth(shift(month, 1))}
              >
                ›
              </button>
              <span className="small-note">
                {month >= forward
                  ? 'дальше не листается: будущие сессии не подтверждены торгами'
                  : ''}
              </span>
            </div>

            <div className="weekdays" aria-hidden="true">
              {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((day) => (
                <span key={day}>{day}</span>
              ))}
            </div>

            <div className="calendar-grid" aria-label={`${MONTHS[index - 1]} ${year}`}>
              {Array.from({ length: firstWeekday }, (_, cell) => (
                <span key={`lead-${cell}`} />
              ))}

              {days.map((day) => {
                const missing = Object.values(day.groups).filter(
                  (state) => state === 'missing',
                ).length;
                const className = [
                  'calendar-day',
                  day.kind === 'nontrade' ? 'nontrade' : '',
                  day.kind === 'open' ? 'open-day' : '',
                  day.kind === 'future' ? 'expected' : '',
                  day.kind === 'session' && missing > 0 ? 'partial' : '',
                ]
                  .filter(Boolean)
                  .join(' ');

                return (
                  <button key={day.date} className={className} type="button" disabled>
                    <span>{Number(day.date.slice(8))}</span>
                    {day.kind === 'session' && (
                      <div className="day-dots">
                        {Object.entries(day.groups).map(([group, state]) => (
                          <i
                            key={group}
                            className={state === 'collected' ? 'complete' : 'partial'}
                          />
                        ))}
                      </div>
                    )}
                    {NOTE[day.kind] !== undefined && <small>{NOTE[day.kind]}</small>}
                  </button>
                );
              })}
            </div>
          </div>

          <aside className="calendar-guide">
            <h3>Обозначения</h3>
            <p>
              Календарь строится по состоявшимся торгам опорной бумаги, поэтому слева от
              сегодняшнего дня — факт, а справа пунктиром — предположение: справочника праздников у
              платформы нет.
            </p>
            <div className="calendar-legend">
              <span>
                <i className="day-symbol complete" />
                Собрано полностью
              </span>
              <span>
                <i className="day-symbol partial" />
                Есть пробелы
              </span>
              <span>
                <i className="day-symbol open-day" />
                Сессия не закрыта
              </span>
              <span>
                <i className="day-symbol nontrade" />
                Торгов не было
              </span>
              <span>
                <i className="day-symbol expected" />
                Ожидается
              </span>
            </div>
            <p className="small-note">Точки под датой — группы в порядке таблицы выше.</p>
          </aside>
        </div>
      </details>
    </section>
  );
}
