/**
 * Журнал последних прогонов.
 *
 * Перенесено из артефакта Open Design `market-data.html`.
 *
 * Ход работы живёт в памяти сборщика и исчезает вместе с ним — так задумано.
 * Журнал лежит в хранилище, поэтому отвечает на вопрос «как прошёл сбор» и
 * после перезапуска (FR-005).
 */

import type { LinkEventDto, RunSummaryDto } from '@/entities/market-data';
import { formatShortStamp } from '@/shared/lib/market-format';

const MODE: Record<string, string> = { daily: 'авто', manual: 'ручной' };

const STATUS: Record<string, string> = {
  finished: 'завершён',
  failed: 'прерван ошибкой',
  interrupted: 'прерван перезапуском',
};

function sessionsLine(run: RunSummaryDto): string {
  const parts = [`${run.sessions.requested} сессий`, `${run.sessions.collected} собрано`];
  if (run.sessions.failed > 0) parts.push(`${run.sessions.failed} с ошибкой`);
  if (run.sessions.skipped > 0) parts.push(`${run.sessions.skipped} пропущено`);
  return parts.join(' · ');
}

export function RunJournal({
  runs,
  events = [],
}: {
  runs: RunSummaryDto[];
  /** Изменения состава инструментов: появление, смена и исчезновение фьючерса. */
  events?: LinkEventDto[];
}) {
  if (runs.length === 0 && events.length === 0) return null;

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

      {events.length > 0 && (
        <>
          {/*
            Изменение состава инструментов названо отдельно от прогонов: без
            этого рост или убыль числа собранных бумаг выглядели бы пропуском
            сбора, а не появлением и исчезновением инструментов (FR-016).
          */}
          <p className="journal-heading">Состав инструментов</p>
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
