/**
 * Сводка полноты по группам источников.
 *
 * Разметка и имена классов перенесены из артефакта Open Design
 * (`market-data.html` и структура, которую строит `design-assets/market-data.js`).
 *
 * Три правила показа, каждое из которых уже стоило проекту дефекта:
 *
 *  - **покрытие и доля значений — две колонки, а не одно число.** Дефект
 *    позиций жил ровно в зазоре между ними: 82 сессии из 82 при пяти
 *    значениях на 21 320 строк (FR-009, FR-012);
 *  - **покрытие не окрашивается как успех.** Доля закрытых сессий ничего не
 *    говорит о качестве собранного (FR-010);
 *  - **у группы без истории числовых полей нет вовсе.** Ноль на их месте
 *    читался бы как «ничего не собрано» (FR-014).
 */

import type { CSSProperties } from 'react';

import type { GroupCoverageDto } from '@/entities/market-data';
import { formatIsoDate, formatRatio, ratioWidth } from '@/shared/lib/market-format';

export function CompletenessTable({
  groups,
  onOpenDetails,
}: {
  groups: GroupCoverageDto[];
  onOpenDetails: (group: GroupCoverageDto) => void;
}) {
  return (
    <table className="completeness-table">
      <colgroup>
        <col style={{ width: '25%' }} />
        <col style={{ width: '26%' }} />
        <col style={{ width: '25%' }} />
        <col style={{ width: '19%' }} />
        <col style={{ width: '5%' }} />
      </colgroup>
      <thead>
        <tr>
          <th scope="col">
            Группа<span>Источник данных</span>
          </th>
          <th scope="col">
            Покрытие<span>Доля сессий окна</span>
          </th>
          <th scope="col">
            Со значениями<span>Доля записанных строк</span>
          </th>
          <th scope="col">
            Период данных<span>От первой до последней сессии</span>
          </th>
          <th scope="col">
            <span className="sr-only">Подробнее</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {groups.map((row, index) => (
          <GroupRow key={row.group} row={row} index={index} onOpenDetails={onOpenDetails} />
        ))}
      </tbody>
    </table>
  );
}

function GroupRow({
  row,
  index,
  onOpenDetails,
}: {
  row: GroupCoverageDto;
  index: number;
  onOpenDetails: (group: GroupCoverageDto) => void;
}) {
  const anomaly = row.looks_collected_but_empty;

  return (
    <tr style={{ '--row': index } as CSSProperties} className={anomaly ? 'anomaly-row' : undefined}>
      <td className="source-cell">
        <span className="group-title">{capitalize(row.title)}</span>
        <span className="group-id">{row.group}</span>
      </td>

      <td data-label="Покрытие">
        {row.has_history ? (
          <>
            <div className="measure">
              <strong>{formatRatio(row.coverage_ratio)}</strong>
              <span className="measure-track" aria-hidden="true">
                <i style={{ width: ratioWidth(row.coverage_ratio) }} />
              </span>
            </div>
            <span className="measure-sub">
              <span className="mono">
                {row.sessions_covered} / {row.window_sessions}
              </span>{' '}
              сессий · не покрыто <span className="mono">{row.gaps}</span>
            </span>
          </>
        ) : (
          <span className="no-history">
            История не ведётся<small>Покрытие не применяется</small>
          </span>
        )}
      </td>

      <td data-label="Со значениями" className="measure-value">
        <div className="measure">
          <strong>{formatRatio(row.value_ratio)}</strong>
          <span className="measure-track value-track" aria-hidden="true">
            <i style={{ width: ratioWidth(row.value_ratio) }} />
          </span>
        </div>
        <span className="measure-sub">{valueNote(row)}</span>
      </td>

      <td className="period-cell" data-label="Период">
        {row.has_history ? (
          <>
            <time dateTime={row.period_from ?? undefined}>{formatIsoDate(row.period_from)}</time>
            <time dateTime={row.period_till ?? undefined}>{formatIsoDate(row.period_till)}</time>
          </>
        ) : (
          <span className="no-history">Текущее состояние</span>
        )}
      </td>

      <td className="detail-cell">
        <button
          className="row-details"
          type="button"
          aria-label={`Сведения: ${capitalize(row.title)}`}
          onClick={() => onOpenDetails(row)}
        >
          ↗
        </button>
      </td>
    </tr>
  );
}

/**
 * Подпись под долей значений.
 *
 * Состояние передаётся текстом, а не только цветом (FR-052): выделение
 * строки цветом читают не все.
 */
function valueNote(row: GroupCoverageDto): string {
  if (row.looks_collected_but_empty) return 'Значения отсутствуют';
  if (row.value_ratio === null) return 'Доля не передана';
  if (row.value_ratio === 1) return 'Значения есть во всех строках';
  return 'Часть строк без значений';
}

/**
 * Название с заглавной буквы.
 *
 * Сервер возвращает «котировки», артефакт подписывает «Котировки». Своей
 * таблицы названий во фронтенде нет намеренно: она стала бы вторым
 * источником истины и разошлась бы при добавлении группы (research.md R6).
 */
function capitalize(title: string): string {
  return title.charAt(0).toUpperCase() + title.slice(1);
}

export { capitalize };
