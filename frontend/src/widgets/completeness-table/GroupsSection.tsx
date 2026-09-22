/**
 * Группы данных: строка группы и раскрытие с исходами источников.
 *
 * Разметка и имена классов перенесены из артефакта Open Design
 * (`market-data.html`, раздел `groups-section`, и `design-assets/
 * market-data-collection-v4/v4.js`).
 *
 * Три правила, каждое из которых уже стоило проекту дефекта:
 *
 *  - **полнота группы считается по каждому источнику.** Успех одного не
 *    закрывает пропуск другого, поэтому раскрытие называет источник поимённо:
 *    «глобальные ряды не собраны» без имени ряда — не диагноз (FR-032);
 *  - **неполнота позиций объясняется числами, а не догадкой.** Фьючерс есть не
 *    у каждой бумаги, и его отсутствие — не пропуск (FR-010, FR-013);
 *  - **у группы без истории полей окна нет вовсе.** Ноль на их месте читался
 *    бы как «ничего не собрано» (FR-014).
 */

import type { GroupCoverageDto, SourceCoverageDto, UniverseDto } from '@/entities/market-data';
import { formatCount, formatIsoDate, formatShortStamp } from '@/shared/lib/market-format';
import { plural } from '@/shared/lib/plural';

type Badge = readonly [kind: 'complete' | 'partial' | 'error', label: string];

export function GroupsSection({
  asofDate,
  groups,
  universe,
  onOpenDetails,
}: {
  asofDate: string;
  groups: GroupCoverageDto[];
  universe: UniverseDto;
  onOpenDetails: (group: GroupCoverageDto) => void;
}) {
  return (
    <section className="groups-section" aria-labelledby="groupsTitle">
      <div className="section-heading">
        <div>
          <h2 id="groupsTitle">Группы данных</h2>
          <p>
            Полнота на <span className="mono">{formatIsoDate(asofDate)}</span>
            {/*
              Состав бумаг считается по последней СОБРАННОЙ сессии, и она может
              быть старше даты сводки. Молчать об этом нельзя: числа группы
              позиций относились бы к другому дню, чем всё остальное на экране.
            */}
            {universe.asof_date !== null && universe.asof_date !== asofDate && (
              <>
                {' · состав бумаг — по последней собранной сессии '}
                <span className="mono">{formatIsoDate(universe.asof_date)}</span>
              </>
            )}
          </p>
        </div>
        <span className="auto-label">
          В автосборе{' '}
          <b>
            {groups.length} из {groups.length}
          </b>
        </span>
      </div>

      <div className="group-columns" aria-hidden="true">
        <span>Группа / источники</span>
        <span>Результат</span>
        <span>Подтверждено из возможного</span>
        <span>Последние данные</span>
        <span />
      </div>

      <div>
        {groups.map((group) => (
          <Group
            key={group.group}
            group={group}
            universe={universe}
            onOpenDetails={onOpenDetails}
          />
        ))}
      </div>
    </section>
  );
}

function Group({
  group,
  universe,
  onOpenDetails,
}: {
  group: GroupCoverageDto;
  universe: UniverseDto;
  onOpenDetails: (group: GroupCoverageDto) => void;
}) {
  const [kind, label] = groupBadge(group);
  const rule = ruleOf(group, universe);

  return (
    <details className="group" data-od-id={`group-${group.group}`}>
      <summary className="group-summary">
        <span>
          <span className="group-title">{capitalize(group.title)}</span>
          <small>{subtitleOf(group, universe)}</small>
        </span>
        <span className={`badge ${kind}`}>{label}</span>
        <span>
          <span className="volume">{volumeOf(group)}</span>
          <small>{group.has_history ? 'сессий подтверждено' : 'источников'}</small>
          <small>{formatCount(group.rows_with_values)} строк со значениями</small>
        </span>
        <span>
          <time dateTime={group.period_till ?? undefined}>
            {group.has_history ? formatIsoDate(group.period_till) : '—'}
          </time>
          <small>{group.has_history ? 'дата значений' : 'текущее состояние'}</small>
        </span>
      </summary>

      <div className="group-detail">
        {(group.requires_audit ?? 0) > 0 && group.has_history && (
          <p className="rule-copy">
            Сохранённые данные на месте. Полнота {group.requires_audit} старых сессий ещё не
            подтверждена: прежние отчёты не доказывают, что все данные источника получены. Для
            проверки выберите эту группу и период в ручном сборе. Новые сессии собираются
            автоматически.
          </p>
        )}
        {rule !== null && <p className="rule-copy">{rule}</p>}

        <table className="source-table">
          <thead>
            <tr>
              <th scope="col">Источник</th>
              <th scope="col">Результат</th>
              <th scope="col">Охват</th>
              <th scope="col">Примечание</th>
            </tr>
          </thead>
          <tbody>
            {group.sources.map((source) => (
              <SourceRow key={source.source_id} source={source} group={group} />
            ))}
          </tbody>
        </table>

        {/*
          Доля строк со значениями в строку группы не выведена намеренно: это
          вторая проверка, и место ей — в сведениях. Но потерять её нельзя,
          дефект позиций жил ровно в зазоре между покрытием и значениями
          (FR-009, FR-012), поэтому вход в сведения есть у каждой группы.
        */}
        <button
          className="secondary-button"
          type="button"
          aria-label={`Сведения: ${capitalize(group.title)}`}
          onClick={() => onOpenDetails(group)}
        >
          Все числа группы
        </button>
      </div>
    </details>
  );
}

function SourceRow({ source, group }: { source: SourceCoverageDto; group: GroupCoverageDto }) {
  const [kind, label] = sourceBadge(source);

  return (
    <tr>
      <td data-label="Источник">
        <strong>{source.title}</strong>
        <small>{source.source_id}</small>
      </td>
      <td data-label="Результат">
        <span className={`badge ${kind}`}>{label}</span>
      </td>
      <td data-label="Охват">
        {group.has_history
          ? `${source.sessions_covered} из ${group.window_sessions ?? source.sessions_covered} сессий`
          : 'текущее состояние'}
      </td>
      <td data-label="Примечание">
        {source.scope === 'daily' ? (
          <DailyReferenceNote source={source} />
        ) : (
          <>
            {SCOPE_NOTE[source.scope] ?? ''}
            <SourceFailures failures={source.failures} total={source.failures_total} />
            {source.requires_audit > 0 && (
              <small>Полнота старых сессий не подтверждена: {source.requires_audit}</small>
            )}
          </>
        )}
      </td>
    </tr>
  );
}

function DailyReferenceNote({ source }: { source: SourceCoverageDto }) {
  if (source.requires_audit > 0) {
    return <small>Старый ответ не подтверждён новым правилом</small>;
  }
  if (source.status === 'failed') {
    return (
      <small>
        Последняя проверка
        {source.last_checked_at !== null && source.last_checked_at !== undefined
          ? ` ${formatShortStamp(source.last_checked_at)}`
          : ''}
        : {source.reason ?? 'источник не ответил'}
      </small>
    );
  }
  if (source.status === 'ok') {
    return (
      <small>
        Полный ответ проверен
        {source.last_checked_at !== null && source.last_checked_at !== undefined
          ? ` ${formatShortStamp(source.last_checked_at)}`
          : ''}
      </small>
    );
  }
  return <small>Проверенного полного ответа ещё нет</small>;
}

/**
 * Неудачи источника поимённо.
 *
 * Перенесено из артефакта Open Design (`renderFailures` в `v4.js`). Без дня и
 * причины «ошибка источника» — это состояние, с которым нечего делать:
 * неизвестно ни когда, ни из-за чего, и проверить у источника нечего.
 */
function SourceFailures({
  failures,
  total,
}: {
  failures: SourceCoverageDto['failures'];
  total: number;
}) {
  if (failures.length === 0) return null;

  return (
    <details className="source-failures">
      <summary>
        Неудачи по дням · {total}
        {total > failures.length && (
          <span className="quiet"> · показаны последние {failures.length}</span>
        )}
      </summary>
      <ol>
        {failures.map((failure) => (
          <li key={failure.session_date}>
            <span className="mono">{formatIsoDate(failure.session_date)}</span>
            <span>{failure.reason ?? 'причина не записана'}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

const SCOPE_NOTE: Record<string, string> = {
  period: 'Один запрос на весь период',
  daily: 'Раз в сутки, к сессии не привязан',
};

/**
 * Итог группы.
 *
 * Расхождение «покрыто, но пусто» важнее любой другой оценки: сессии закрыты,
 * а значений нет, и догон пропущенных сессий этого не исправит.
 */
function groupBadge(group: GroupCoverageDto): Badge {
  if (group.looks_collected_but_empty) return ['error', 'Значения отсутствуют'];
  // Ошибкой называется ошибка, а не всякая неполнота: у падавшего источника
  // есть записанные неудачи, и они названы днём и причиной.
  if (group.sources.some((source) => source.status === 'failed'))
    return ['error', 'Ошибка источника'];
  if ((group.requires_audit ?? 0) > 0)
    return ['partial', group.has_history ? 'История не проверена' : 'Нужна проверка'];
  if (group.sources.some((source) => source.status === 'partial')) return ['partial', 'Частично'];
  if (group.has_history && (group.gaps ?? 0) > 0) return ['partial', 'Частично'];
  return ['complete', 'Собрано'];
}

/**
 * Итог источника.
 *
 * «Ошибка» и «частично» — разные состояния: источник, не собравший ни одной
 * сессии окна, и источник с пропусками требуют разных действий.
 */
function sourceBadge(source: SourceCoverageDto): Badge {
  if (source.status !== 'failed' && source.requires_audit > 0) return ['partial', 'Нужна проверка'];
  if (source.status === 'ok') return ['complete', 'Собрано'];
  if (source.status === 'partial') return ['partial', 'Частично'];
  return ['error', 'Ошибка'];
}

function volumeOf(group: GroupCoverageDto): string {
  if (group.has_history) return `${group.sessions_covered} / ${group.window_sessions}`;
  const ok = group.sources.filter((source) => source.status === 'ok').length;
  return `${ok} / ${group.sources.length}`;
}

function subtitleOf(group: GroupCoverageDto, universe: UniverseDto): string {
  const count = `${group.sources.length} ${plural(group.sources.length, 'источник', 'источника', 'источников')}`;

  if (group.group === 'positions') {
    // «0 из 0» читалось бы как «фьючерса нет ни у одной бумаги», хотя состав
    // просто не посчитан: признак торгуемости выводится из наблюдений, и без
    // собранной сессии его взять неоткуда (FR-019a).
    if (!isKnown(universe)) return `${count} · состав бумаг не посчитан`;
    return `${count} · фьючерс есть у ${universe.assets_with_futures} из ${universe.assets} бумаг`;
  }
  if (!group.has_history) return `${count} · текущее состояние`;

  const failed = group.sources.filter((source) => source.status === 'failed').length;
  if (failed > 0) {
    return `${count} · ${failed === 1 ? 'один с ошибкой' : `${failed} с ошибкой`}`;
  }
  return count;
}

/**
 * Посчитан ли состав бумаг.
 *
 * Сервер отвечает датой сессии, по которой он посчитан, и `null` означает, что
 * собранных сессий в окне нет вовсе. Ноль бумаг и неизвестный состав — разные
 * утверждения, и путать их нельзя: на этом различии держится FR-019a.
 */
function isKnown(universe: UniverseDto): boolean {
  return universe.asof_date !== null;
}

/**
 * Правило, по которому группа считается полной.
 *
 * Пишется там, где человек задаёт вопрос, а не в документации: «почему у
 * фьючерсов меньше сессий» — вопрос к строке группы.
 */
function ruleOf(group: GroupCoverageDto, universe: UniverseDto): string | null {
  if (group.group === 'positions') {
    if (!isKnown(universe)) {
      return (
        'Состав бумаг считается по последней собранной сессии, а собранных пока нет: ' +
        'сколько бумаг с фьючерсом — неизвестно. Окно у группы своё — ' +
        `${group.window_sessions} сессий: глубже позиции модели не нужны.`
      );
    }
    return (
      `Позиции бывают не по всем бумагам: фьючерс есть у ${universe.assets_with_futures} ` +
      `из ${universe.assets}. Окно у группы своё — ${group.window_sessions} сессий: ` +
      'глубже позиции модели не нужны.'
    );
  }
  if (!group.has_history) {
    return 'Справочник отражает текущее состояние, истории у него нет — считать по сессиям нечего.';
  }
  if (group.sources.length > 1) {
    return (
      `Полнота считается по каждому из ${group.sources.length} источников: ` +
      'успех одного не закрывает пропуск другого.'
    );
  }
  return null;
}

/**
 * Название с заглавной буквы.
 *
 * Сервер возвращает «котировки», артефакт подписывает «Котировки». Своей
 * таблицы названий во фронтенде нет намеренно: она стала бы вторым источником
 * истины и разошлась бы при добавлении группы (research.md R6).
 */
export function capitalize(title: string): string {
  return title.charAt(0).toUpperCase() + title.slice(1);
}
