/**
 * Ход по сессиям прогона: шкала, легенда и причины пропусков.
 *
 * Перенесено из артефакта Open Design `market-data.html`.
 *
 * Шкала показывает **весь объём прогона**, а не только пройденное: иначе не
 * видно, сколько осталось. Цвет говорит об исходе каждой сессии, а не просто
 * «сделано». Пропуск — отдельный исход, и у каждого названа причина: без неё
 * человек видит дыру и не знает, ждать ему или вмешиваться (FR-001, FR-002).
 *
 * Прогон из одной сессии шкалой по сессиям не показывается: единственный
 * сегмент читался бы как «сто процентов». Там работа видна по источникам.
 */

import type { SessionProgressDto, SessionSkipDto } from '@/entities/market-data';
import { formatIsoDate } from '@/shared/lib/market-format';

const FILL: Record<string, string> = {
  collected: 'fill-complete',
  partial: 'fill-partial',
  failed: 'fill-error',
  skipped: 'fill-skipped',
  pending: 'fill-wait',
};

const LABEL: Record<string, string> = {
  collected: 'собрано',
  partial: 'частично',
  skipped: 'пропущено',
  pending: 'осталось',
};

const SKIP_TITLE: Record<string, string> = {
  withheld_until_close: 'отложена до закрытия сессии',
  retry_delay: 'выдержка после неудачи',
  attempts_exhausted: 'исчерпан предел попыток',
  gap_over_limit: 'разрыв больше предела',
};

/** «2 ошибка» — единственное склоняемое слово легенды. */
function errors(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'ошибка';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'ошибки';
  return 'ошибок';
}

function skipWord(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'Почему пропущена 1 сессия';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14))
    return `Почему пропущены ${count} сессии`;
  return `Почему пропущено ${count} сессий`;
}

/**
 * Сегментов, после которых дорожка рисуется сплошной.
 *
 * За порогом сегмент тоньше собственного зазора: при 216 сессиях 215 зазоров
 * по 3px дают 645 пикселей там, где дорожке отведено около 400. Сегменты
 * схлопываются в ноль, и вместо шкалы видна ровная полоса фона.
 */
const DENSE_FROM = 40;

export function SessionProgress({
  sessions,
  skips,
}: {
  sessions: SessionProgressDto;
  skips: SessionSkipDto[];
}) {
  const processed = sessions.collected + sessions.partial + sessions.failed;

  const counts: [string, number][] = [
    ['collected', sessions.collected],
    ['partial', sessions.partial],
    ['failed', sessions.failed],
    ['skipped', sessions.skipped],
    ['pending', sessions.pending],
  ];

  return (
    <div className="session-progress">
      <div className="metric-heading">
        <span>Сессии прогона</span>
        <strong className="count-big">
          {processed} <small>из {sessions.requested}</small>
        </strong>
      </div>

      {/*
        Все числа озвучиваются разом: по цвету их читают не все, а разделение
        «собрано / с ошибкой / пропущено / осталось» — суть этой шкалы.
      */}
      <div
        className={`segmented-track${sessions.requested > DENSE_FROM ? ' dense' : ''}`}
        role="img"
        aria-label={
          `Собрано ${sessions.collected} из ${sessions.requested}; ` +
          `с ошибкой ${sessions.failed}; пропущено ${sessions.skipped}; ` +
          `осталось ${sessions.pending}`
        }
      >
        {sessions.outcomes.map((row) => (
          <span
            key={row.session_date}
            className={FILL[row.outcome]}
            style={{ flex: 1 }}
            title={`${formatIsoDate(row.session_date)} — ${LABEL[row.outcome] ?? row.outcome}`}
          />
        ))}
        {Array.from({ length: sessions.pending }, (_, index) => (
          <span key={`pending-${index}`} className={FILL.pending} style={{ flex: 1 }} />
        ))}
      </div>

      <div className="outcome-legend">
        {counts
          .filter(([, count]) => count > 0)
          .map(([outcome, count]) => (
            <span key={outcome}>
              <i className={FILL[outcome]} />
              {count} {outcome === 'failed' ? errors(count) : LABEL[outcome]}
            </span>
          ))}
      </div>

      {skips.length > 0 && (
        <details className="skip-details" data-od-id="skipped-sessions">
          <summary>{skipWord(skips.length)}</summary>
          <ul className="skip-list">
            {skips.map((skip) => (
              <li key={`${skip.session_date}-${skip.reason}`}>
                <span className="mono">{formatIsoDate(skip.session_date)}</span>
                <span>
                  {SKIP_TITLE[skip.reason] ?? skip.reason}
                  {skip.detail !== null && `: ${skip.detail}`}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
