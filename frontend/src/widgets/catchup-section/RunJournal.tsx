/**
 * Журнал последних прогонов.
 *
 * Перенесено из артефакта Open Design `market-data.html`.
 *
 * Ход работы живёт в памяти сборщика и исчезает вместе с ним — так задумано.
 * Журнал лежит в хранилище, поэтому отвечает на вопрос «как прошёл сбор» и
 * после перезапуска (FR-005).
 */

import type { LinkEventDto, RunsDto, RunSummaryDto } from '@/entities/market-data';
import { formatIsoDate, formatShortStamp } from '@/shared/lib/market-format';

/**
 * Причина пропуска словами.
 *
 * Перечень закрытый, и его объявляет сервер: вторая таблица причин в
 * интерфейсе однажды разошлась бы с первой (FR-002).
 */
const SKIP_REASON: Record<string, string> = {
  withheld_until_close: 'отложена до закрытия сессии',
  retry_delay: 'выдержка после неудачи',
  attempts_exhausted: 'исчерпан предел попыток',
  gap_over_limit: 'разрыв больше предела',
};

const MODE: Record<string, string> = { daily: 'авто', manual: 'ручной' };

const STATUS: Record<string, string> = {
  finished: 'завершён',
  failed: 'прерван ошибкой',
  interrupted: 'прерван перезапуском',
};

function sessionsLine(run: RunSummaryDto): string {
  // Проход без сессий — проверка календаря, а не несостоявшийся сбор: «0
  // сессий · 0 собрано» читалось как сбой (артефакт, журнал прогонов).
  if (run.sessions.requested === 0) return 'календарь проверен · новых сессий нет';
  const parts = [`${run.sessions.requested} сессий`, `${run.sessions.collected} собрано`];
  if (run.sessions.partial > 0) parts.push(`${run.sessions.partial} частично`);
  if (run.sessions.failed > 0) parts.push(`${run.sessions.failed} с ошибкой`);
  if (run.sessions.skipped > 0) parts.push(`${run.sessions.skipped} пропущено`);
  if (run.sessions.pending > 0) parts.push(`${run.sessions.pending} не завершено`);
  if (run.history_limited) parts.push('точный итог старой записи недоступен');
  return parts.join(' · ');
}

export function RunJournal({
  runs,
  events = [],
  eventsTotal = 0,
  skips = [],
  skipsTotal = 0,
}: {
  runs: RunSummaryDto[];
  /** Изменения состава инструментов: появление, смена и исчезновение фьючерса. */
  events?: LinkEventDto[];
  /** Сколько изменений состава всего: списки ограничены, и об остатке надо сказать. */
  eventsTotal?: number;
  /** Причины пропусков из хранилища: они переживают перезапуск сборщика. */
  skips?: RunsDto['skips'];
  skipsTotal?: number;
}) {
  if (runs.length === 0 && events.length === 0 && skips.length === 0) return null;

  return (
    <details className="run-journal" data-od-id="run-journal">
      <summary>
        Последние прогоны <span className="quiet">· из журнала сбора, переживает перезапуск</span>
      </summary>
      <ol>
        {runs.map((run) => (
          <li key={run.run_id}>
            <span className="mono">{formatShortStamp(run.started_at)}</span>
            <span>{MODE[run.mode] ?? run.mode}</span>
            <span
              className={`journal-outcome${run.status === 'finished' ? '' : ' error'}`}
              title={STATUS[run.status] ?? run.status}
            >
              {sessionsLine(run)}
              {run.failures[0] !== undefined &&
                `: ${run.failures[0].title} — ${run.failures[0].reason ?? 'без причины'}`}
            </span>
          </li>
        ))}
      </ol>

      {skips.length > 0 && (
        <>
          {/*
            Причина пропуска живёт в хранилище, а не в памяти сборщика: без неё
            человек видит дыру и не знает, ждать ему или вмешиваться (FR-002).
          */}
          <p className="journal-heading">
            Пропущенные сессии
            {skipsTotal > skips.length && (
              <span className="quiet">
                {' '}
                · показаны последние {skips.length} из {skipsTotal}
              </span>
            )}
          </p>
          <ol>
            {skips.map((skip) => (
              <li key={`${skip.session_date}-${skip.decided_at}`}>
                <span className="mono">{formatIsoDate(skip.session_date)}</span>
                <span>{SKIP_REASON[skip.reason] ?? skip.reason}</span>
                <span className="journal-outcome">{skip.detail ?? ''}</span>
              </li>
            ))}
          </ol>
        </>
      )}

      {events.length > 0 && (
        <>
          {/*
            Изменение состава инструментов названо отдельно от прогонов: без
            этого рост или убыль числа собранных бумаг выглядели бы пропуском
            сбора, а не появлением и исчезновением инструментов (FR-016).
          */}
          <p className="journal-heading">
            Состав инструментов
            {eventsTotal > events.length && (
              <span className="quiet">
                {' '}
                · показаны последние {events.length} из {eventsTotal}
              </span>
            )}
          </p>
          <ol>
            {events.map((event) => (
              <li key={`${event.at}-${event.ticker}`}>
                <span className="mono">{formatShortStamp(event.at)}</span>
                <span>{event.ticker}</span>
                <span className={`journal-outcome${event.kind === 'closed' ? ' error' : ''}`}>
                  {event.detail}
                </span>
              </li>
            ))}
          </ol>
        </>
      )}
    </details>
  );
}
