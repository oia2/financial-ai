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

import { Fragment, useEffect, useRef, useState } from 'react';

import type { CalendarDayDto } from '@/entities/market-data';
import { useCalendar } from '@/entities/market-data';
import { formatIsoDate } from '@/shared/lib/market-format';

/** Названия групп в сведениях о дате. Порядок — как в таблице групп. */
const GROUP_TITLE: Record<string, string> = {
  quotes: 'Котировки',
  aggregates: 'Агрегаты торгов',
  global: 'Глобальные ряды',
  positions: 'Позиции по фьючерсам',
  reference: 'Справочники',
};

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

/**
 * Совпадает ли пояс зрителя с биржевым.
 *
 * Москва — UTC+3 круглый год. Сравнение по смещению, а не по названию зоны:
 * зон с тем же смещением несколько, и все они для нас одно и то же.
 */
function moscowIsLocal(): boolean {
  return new Date().getTimezoneOffset() === -180;
}

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
  collecting = null,
  proxySecurity = 'SBER · TQBR',
  skipReasons = {},
  onCollect,
}: {
  /** Сессия, которую возьмёт следующий сбор. Из торгового календаря (FR-024a). */
  nextSession: string | null;
  /** Порог сбора текущей сессии: время и биржевое время. */
  threshold: { local: string; exchange: string };
  lastClosed: string | null;
  paused: boolean;
  /** Сессия, которую собирают прямо сейчас. Помечается отдельно (FR-024). */
  collecting?: string | null;
  /** Опорная бумага календаря: по её торгам он и строится. */
  proxySecurity?: string;
  /** Причины пропусков по датам: из журнала, поэтому переживают перезапуск. */
  skipReasons?: Record<string, string>;
  /** Поставить одну сессию в ручной сбор. */
  onCollect?: (date: string) => void;
}) {
  const [month, setMonth] = useState(() => monthKey(new Date()));
  const [opened, setOpened] = useState<CalendarDayDto | null>(null);
  const calendar = useCalendar(month);

  const days = calendar.data?.days ?? [];
  const [year, index] = parseMonth(month);
  const firstWeekday = (new Date(year, index - 1, 1).getDay() + 6) % 7;
  const forward = shift(monthKey(new Date()), 1);
  // Назад — не дальше самой ранней известной сессии: пустые месяцы иначе
  // листались бы бесконечно, а показать там нечего.
  const backward = calendar.data?.earliest_month ?? null;
  const atStart = backward !== null && month <= backward;

  return (
    <section className="schedule-section" aria-labelledby="scheduleTitle">
      <div className="section-heading">
        <h2 id="scheduleTitle">Расписание и календарь</h2>
      </div>

      <ul className="schedule-facts">
        <li>
          <span>Следующий сбор</span>
          <strong>
            {paused ? (
              <>
                не будет <span className="msk">пока автосбор на паузе</span>
              </>
            ) : (
              <>
                {`сегодня после ${threshold.local} `}
                {/*
                  Московское время — только дополнение к биржевому порогу и
                  только когда пояс зрителя не совпадает с биржевым: иначе оно
                  повторяет уже сказанное (FR-022). Пробел перед скобкой —
                  часть текста, а не отступ: без него строка слипается.
                */}
                {!moscowIsLocal() && <span className="msk">(порог {threshold.exchange})</span>}
              </>
            )}
          </strong>
        </li>
        <li>
          {/*
            Какую сессию возьмёт ближайший сбор. Отдельной строкой, потому что
            при отставании это дата из прошлого: рядом со словом «следующий»
            она читалась бы как ошибка.
          */}
          <span>Возьмёт сессию</span>
          <strong className="mono">
            {nextSession === null ? '—' : formatIsoDate(nextSession)}
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
                disabled={atStart}
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
                {atStart
                  ? 'дальше назад истории нет'
                  : month >= forward
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
                const active = day.date === collecting;
                const className = [
                  'calendar-day',
                  day.kind === 'nontrade' ? 'nontrade' : '',
                  day.kind === 'open' && !active ? 'open-day' : '',
                  day.kind === 'future' ? 'expected' : '',
                  active || (day.kind === 'session' && missing > 0) ? 'partial' : '',
                ]
                  .filter(Boolean)
                  .join(' ');

                return (
                  <button
                    key={day.date}
                    className={className}
                    type="button"
                    // Нажимается только состоявшаяся сессия: о будущем и о
                    // неторговом дне рассказывать нечего.
                    disabled={day.kind !== 'session'}
                    aria-label={`Сессия ${formatIsoDate(day.date)}`}
                    onClick={() => setOpened(day)}
                  >
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
                    {active ? (
                      <small>собирается сейчас</small>
                    ) : (
                      NOTE[day.kind] !== undefined && <small>{NOTE[day.kind]}</small>
                    )}
                  </button>
                );
              })}
              {/*
                Добираем сетку до шести рядов: иначе февраль короче марта, и
                страница прыгает под курсором при листании.
              */}
              {Array.from({ length: Math.max(0, 42 - firstWeekday - days.length) }, (_, cell) => (
                <span key={`tail-${cell}`} />
              ))}
            </div>
          </div>

          <aside className="calendar-guide">
            <h3>Обозначения</h3>
            <p>
              Календарь строится по состоявшимся торгам опорной бумаги ({proxySecurity}), поэтому
              слева от сегодняшнего дня — факт, а справа пунктиром — предположение: справочника
              праздников у платформы нет.
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
                <i className="day-symbol failed" />
                Ошибка источника
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

      <DateDialog
        day={opened}
        reason={opened === null ? undefined : skipReasons[opened.date]}
        onCollect={onCollect}
        onClose={() => setOpened(null)}
      />
    </section>
  );
}

/**
 * Сведения о дате: что за неё собрано и можно ли собрать сейчас.
 *
 * Перенесено из артефакта (`dateDialog`). Отвечает на вопрос, который
 * календарь ставит, но сам не закрывает: точка под датой говорит «не собрано»,
 * а чего именно не хватает — видно только здесь.
 */
function DateDialog({
  day,
  reason,
  onCollect,
  onClose,
}: {
  day: CalendarDayDto | null;
  reason: string | undefined;
  onCollect: ((date: string) => void) | undefined;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    if (day !== null && !dialog.open) dialog.showModal();
    if (day === null && dialog.open) dialog.close();
  }, [day]);

  const missing =
    day === null ? 0 : Object.values(day.groups).filter((state) => state === 'missing').length;

  return (
    <dialog className="drawer" ref={ref} onClose={onClose} aria-labelledby="dateTitle">
      <div className="drawer-heading">
        <h2 id="dateTitle">{day === null ? 'Сессия' : `Сессия ${formatIsoDate(day.date)}`}</h2>
        <button
          className="icon-button"
          type="button"
          aria-label="Закрыть сведения о дате"
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <div className="drawer-body">
        {day !== null && (
          <div className="date-group">
            <h3>Что собрано</h3>
            <div className="date-source">
              {Object.entries(day.groups).map(([group, state]) => (
                <Fragment key={group}>
                  <span>{GROUP_TITLE[group] ?? group}</span>
                  <span className={`badge ${state === 'collected' ? 'complete' : 'partial'}`}>
                    {state === 'collected' ? 'Собрано' : 'Не собрано'}
                  </span>
                </Fragment>
              ))}
            </div>
            <p>
              {reason !== undefined
                ? `Причина пропуска: ${reason}.`
                : missing === 0
                  ? 'Сессия собрана полностью.'
                  : `Не собрано групп: ${missing}.`}
            </p>
          </div>
        )}
      </div>

      <div className="drawer-footer">
        <button className="secondary-button" type="button" onClick={onClose}>
          Закрыть
        </button>
        {/*
          Собрать одну сессию — тот же ручной сбор, только диапазоном в один
          день. Второго способа делать то же самое заводить незачем.
        */}
        {day !== null && missing > 0 && onCollect !== undefined && (
          <button
            className="primary-button"
            type="button"
            onClick={() => {
              onCollect(day.date);
              onClose();
            }}
          >
            Собрать эту сессию
          </button>
        )}
      </div>
    </dialog>
  );
}
