/**
 * Уведомление «покрыто, но пусто».
 *
 * Ради этого расхождения сводка и заводилась: группа выглядит собранной, а
 * значений в ней нет. Показывается дважды — здесь и выделением строки в
 * таблице (FR-016), потому что человек, читающий таблицу чисел, зазор между
 * двумя долями не замечает. Именно так дефект позиций и прожил незамеченным.
 *
 * Вывод делает сервер полем `looks_collected_but_empty`: порог не должен жить
 * второй жизнью в интерфейсе (research.md R2.1).
 */

import type { GroupCoverageDto } from '@/entities/market-data';
import { formatRatio } from '@/shared/lib/market-format';

import { capitalize } from './CompletenessTable';

export function EmptyValuesAlert({ groups }: { groups: GroupCoverageDto[] }) {
  const affected = groups.filter((row) => row.looks_collected_but_empty);
  const first = affected[0];
  if (first === undefined) return null;

  return (
    <section className="integrity-alert" role="alert">
      <div>
        <h2>Сессии покрыты. Значения отсутствуют.</h2>
        <p>
          {affected.map((row) => capitalize(row.title)).join(', ')}: полное покрытие не означает,
          что данные собраны. Догон пропущенных сессий сам по себе это расхождение не исправит.
        </p>
      </div>
      <div className="alert-comparison">
        <div>
          <strong>{formatRatio(first.coverage_ratio)}</strong>
          <small>покрытие</small>
        </div>
        <span aria-hidden="true">→</span>
        <div>
          <strong>{formatRatio(first.value_ratio)}</strong>
          <small>строк со значениями</small>
        </div>
      </div>
    </section>
  );
}
