/**
 * Журнал событий идущего прогона.
 *
 * Перенесено из артефакта Open Design (`event-log` в `v4.js`).
 *
 * Отвечает на вопрос, на который лента источников не отвечает: **что было
 * последние минуты**. Лента показывает нынешнее положение дел и произошедшее
 * стирает — собравшийся источник в ней просто становится галочкой, а
 * пропущенная полчаса назад сессия не видна вовсе.
 *
 * Это НЕ журнал прогонов: тот лежит ниже, читается из таблицы исходов и
 * переживает перезапуск. Этот живёт в памяти сборщика вместе с самим прогоном
 * и вместе с ним исчезает.
 */

import type { CatchupStateDto } from '@/entities/market-data';
import { formatShortStamp } from '@/shared/lib/market-format';

export function EventLog({ log }: { log: CatchupStateDto['log'] }) {
  if (log.length === 0) return null;

  return (
    <details className="event-log" data-od-id="event-log">
      <summary>
        Журнал событий <span className="quiet">· последние события прогона</span>
      </summary>
      <ol>
        {log.map((event) => (
          <li key={`${event.at}-${event.text}`}>
            <span className="mono">{formatShortStamp(event.at)}</span>
            <span>{event.text}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
