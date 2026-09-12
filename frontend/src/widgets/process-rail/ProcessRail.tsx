/**
 * Общий баннер идущего сбора.
 *
 * Виден в обоих разделах, пока прогон идёт или останавливается, и скрыт в
 * остальных состояниях (FR-006). Идущий догон — процесс всего приложения, а
 * не одного экрана: человек, ушедший в портфель, не должен терять его из
 * виду.
 *
 * Порядок «состояние → пояснение → действие» тот же, что у баннера
 * синхронизации портфеля из фичи 001 (FR-007): два баннера в одной шапке
 * обязаны читаться одинаково.
 *
 * Разметка перенесена из артефакта (`global-process-banner`).
 */

import { RouteLink } from '@/app/router';
import { isCatchupActive, type CatchupStateDto } from '@/entities/market-data';

export function ProcessRail({
  state,
  route,
}: {
  state: CatchupStateDto | undefined;
  route: 'portfolio' | 'market-data';
}) {
  if (state === undefined || !isCatchupActive(state.status)) return null;

  const stopping = state.status === 'stopping';

  return (
    <div className="process-rail" role="status">
      <RouteLink route="market-data">
        <span className={`process-glyph${stopping ? ' is-stopping' : ''}`} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>

        <span>{stopping ? 'Завершаем текущую сессию' : 'Идёт сбор рыночных данных'}</span>

        <span className="rail-count">
          {state.closed} / {state.requested}
        </span>

        {/* На своей же странице ссылка «перейти» смысла не несёт. */}
        {route !== 'market-data' && <span className="rail-link">Открыть раздел →</span>}
      </RouteLink>
    </div>
  );
}
