/**
 * Общий баннер идущих процессов.
 *
 * Процессов **два**, и они независимы: догон рыночных данных и выполнение
 * ранжирования. Ни один не вытесняет другой — оба могут идти одновременно, и
 * скрыть один ради другого значило бы соврать о происходящем
 * (contracts/ui-states.md, `global-process-banner`).
 *
 * Виден из любого раздела, пока процесс идёт (FR-006): человек, ушедший в
 * портфель, не должен терять работу системы из виду. Порядок «состояние →
 * пояснение → действие» тот же, что у баннера синхронизации портфеля из фичи
 * 001 (FR-007).
 *
 * Разметка перенесена из артефакта (`financial-ai-processes.js`).
 */

import { RouteLink, type RouteName } from '@/app/router';
import type { DailyMlStatusDto } from '@/entities/daily-ml';
import { isCatchupActive, type CatchupStateDto } from '@/entities/market-data';
import { formatIsoDate } from '@/shared/lib/market-format';

function Glyph({ stopping = false }: { stopping?: boolean }) {
  return (
    <span className={`process-glyph${stopping ? ' is-stopping' : ''}`} aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

export function ProcessRail({
  state,
  dailyMl,
  route,
}: {
  state: CatchupStateDto | undefined;
  dailyMl: DailyMlStatusDto | undefined;
  route: RouteName;
}) {
  const catchupActive = state !== undefined && isCatchupActive(state.status);
  const inference = dailyMl?.current ?? null;

  if (!catchupActive && inference === null) return null;

  const stopping = state?.status === 'stopping';
  const processCount = (catchupActive ? 1 : 0) + (inference !== null ? 1 : 0);

  return (
    <div
      className="process-rail"
      role="status"
      data-od-id="global-process-banner"
      data-process-count={processCount}
    >
      {catchupActive && state !== undefined && (
        <RouteLink route="market-data" data-od-id="global-catchup-process">
          <Glyph stopping={stopping} />
          <span className="rail-title">
            {stopping ? 'Догон данных · завершаем текущую сессию' : 'Догон данных продолжается'}
          </span>
          <span className="rail-count">
            {state.closed} / {state.requested} закрыто
          </span>
          {/* На своей же странице ссылка «перейти» смысла не несёт. */}
          {route !== 'market-data' && <span className="rail-link">К процессу →</span>}
        </RouteLink>
      )}

      {inference !== null && (
        <RouteLink route="daily-ml" data-od-id="global-ranking-process">
          <Glyph />
          <span className="rail-title">Выполняется ранжирование</span>
          <span className="rail-count">{formatIsoDate(inference.asof_date)}</span>
          {route !== 'daily-ml' && <span className="rail-link">К процессу →</span>}
        </RouteLink>
      )}
    </div>
  );
}
