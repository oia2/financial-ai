/**
 * Журнал последних прогонов.
 *
 * Перенесено из артефакта Open Design `market-data.html`.
 *
 * Ход работы живёт в памяти сборщика и исчезает вместе с ним — так задумано.
 * Журнал лежит в хранилище, поэтому отвечает на вопрос «как прошёл сбор» и
 * после перезапуска (FR-005).
 */

import type { RunSummaryDto } from '@/entities/market-data';
import { formatShortStamp } from '@/shared/lib/market-format';

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

export function RunJournal({ runs }: { runs: RunSummaryDto[] }) {
  if (runs.length === 0) return null;

  // Только прогоны. Списки пропущенных сессий и изменений состава убраны
  // решением владельца (FR-024g): они копили сотни давно закрытых записей.
  // Причина пропуска остаётся в календаре по дню (FR-002).
  return (
    <details className="run-journal" data-od-id="run-journal">
      <summary>Последние прогоны</summary>
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
    </details>
  );
}
