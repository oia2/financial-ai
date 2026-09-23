/**
 * План источников текущей сессии.
 *
 * Перенесено из артефакта Open Design `market-data.html`.
 *
 * Два правила, без которых лента бесполезна:
 *
 *  - **порядок слева направо — порядок выполнения.** Виден не только идущий
 *    источник, но и следующий: долгий шаг перестаёт быть неотличимым от
 *    зависания (FR-003);
 *  - **счётчик считает ровно то, что показано в ленте** (владелец,
 *    2026-09-23): «1 из 4» при восьми строках читалось как ошибка счёта.
 *    Источник, идущий раз на прогон, выполнен до сессий и стоит в счёте
 *    выполненным. Источник вне своего окна в ленту не попадает (FR-033c).
 */

import type { RunSourceDto } from '@/entities/market-data';
import { formatIsoDate } from '@/shared/lib/market-format';

const GLYPH: Record<string, string> = {
  done: '✓',
  failed: '✕',
  running: '●',
  skipped: '–',
  pending: '',
};

export function SourceRail({
  sessionDate,
  sources,
  running,
}: {
  sessionDate: string;
  sources: RunSourceDto[];
  /** У идущего прогона первый ожидающий источник помечается следующим. */
  running: boolean;
}) {
  const perSession = sources;

  const passed = perSession.filter(
    (source) => source.state === 'done' || source.state === 'failed',
  ).length;
  const failed = perSession.filter((source) => source.state === 'failed').length;

  // «Следующим» бывает только ПОСЕССИОННЫЙ источник: диапазонный и суточный в
  // очередь сессии не входят и ждущими остаются всё время, пока прогон идёт.
  // Торговый календарь синхронизируется один раз перед циклом сессий и стоял
  // первым ожидающим до самого конца прогона (FR-056).
  const nextIndex = running
    ? sources.findIndex((source) => source.scope === 'session' && source.state === 'pending')
    : -1;

  return (
    <div className="source-rail" data-od-id="source-rail">
      <div className="rail-head">
        <b>
          Источники сессии {formatIsoDate(sessionDate)}: {passed} из {perSession.length}
          {failed > 0 && `, ${failed} с ошибкой`}
        </b>
      </div>

      <ul className="rail">
        {sources.map((source, index) => {
          // Пометка области у строки НЕ дублируется: она уже сказана над
          // лентой одной фразой («плюс глобальные ряды — на весь период»).
          // Повторённая у каждой строки, она соседствует с её собственной
          // подписью — «собран ранее · на весь период», — и читается как
          // вторая характеристика работы, которой нет.
          const flag =
            index === nextIndex ? 'следующий' : source.state === 'running' ? 'идёт' : undefined;

          return (
            <li
              key={source.source_id}
              className={['rail-item', source.state, index === nextIndex ? 'next' : '']
                .filter(Boolean)
                .join(' ')}
            >
              <i aria-hidden="true">{GLYPH[source.state] ?? ''}</i>
              {source.title}
              {source.detail !== undefined && <span className="rail-note">{source.detail}</span>}
              {flag !== undefined && <span className="rail-flag">{flag}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
