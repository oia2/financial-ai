/**
 * Группы данных: строка группы и раскрытие с исходами источников.
 *
 * Разметка и имена классов перенесены из артефакта Open Design
 * (`market-data.html`, раздел `groups-section`, и `design-assets/
 * market-data-collection-v4/v4.js`, `renderGroups`).
 *
 * Правила, каждое из которых уже стоило проекту дефекта:
 *
 *  - **состояние выбирает сервер** из закрытого перечня (FR-024e): остановка,
 *    перезапуск и идущий сбор — не «ошибка источника», и правило старшинства
 *    не повторяется здесь второй раз;
 *  - **на экране только факты из данных** (FR-024d): под состоянием одна
 *    строка — сколько не хватает и последняя причина с датой. Шаблонных
 *    пояснений о правилах счёта нет;
 *  - **у группы без истории полей окна нет вовсе.** Ноль на их месте читался
 *    бы как «ничего не собрано» (FR-014).
 */

import type {
  CoverageState,
  GroupCoverageDto,
  SourceCoverageDto,
  UniverseDto,
} from '@/entities/market-data';
import { formatIsoDate } from '@/shared/lib/market-format';
import { plural } from '@/shared/lib/plural';

type Badge = readonly [kind: 'complete' | 'partial' | 'error', label: string];

/** Подпись состояния — из артефакта (`v4.js`, перечень над `GROUPS`). */
const STATE_BADGE: Record<CoverageState, Badge> = {
  empty: ['error', 'Значения отсутствуют'],
  running: ['partial', 'Идёт сбор'],
  source_error: ['error', 'Ошибка источника'],
  internal_error: ['error', 'Ошибка сбора'],
  interrupted: ['partial', 'Прервано'],
  missing: ['partial', 'Не собрано'],
  complete: ['complete', 'Собрано'],
};

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
            Данные по <span className="mono">{formatIsoDate(asofDate)}</span>
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
        <span>Группа</span>
        <span>Состояние</span>
        <span>Сессии</span>
        <span>Данные по</span>
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
  const fact = factOf(group);

  return (
    <details className="group" data-od-id={`group-${group.group}`}>
      <summary className="group-summary">
        <span>
          <span className="group-title">{capitalize(group.title)}</span>
          <small>{subtitleOf(group, universe)}</small>
        </span>
        <span className="state-cell">
          <span className={`badge ${kind}`}>{label}</span>
          {fact !== '' && <small className="state-fact">{fact}</small>}
        </span>
        <span className="volume">{volumeOf(group)}</span>
        <span>
          <time dateTime={lastOf(group) ?? undefined}>{formatIsoDate(lastOf(group))}</time>
        </span>
      </summary>

      <div className="group-detail">
        <table className="source-table">
          <thead>
            <tr>
              <th scope="col">Источник</th>
              <th scope="col">Состояние</th>
              <th scope="col">Сессии</th>
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
      <td data-label="Состояние">
        <span className={`badge ${kind}`}>{label}</span>
      </td>
      <td data-label="Сессии">
        {group.has_history
          ? `${source.sessions_covered} из ${group.window_sessions ?? source.sessions_covered}`
          : checkedOf(source)}
      </td>
      <td data-label="Примечание">
        {!group.has_history && kind === 'error' && source.reason ? (
          <small>{source.reason}</small>
        ) : (
          <SourceFailures failures={source.failures} total={source.failures_total} />
        )}
      </td>
    </tr>
  );
}

/**
 * Неудачи источника поимённо.
 *
 * Перенесено из артефакта Open Design (`renderFailures` в `v4.js`). Без дня и
 * причины состояние — это состояние, с которым нечего делать: неизвестно ни
 * когда, ни из-за чего, и проверить у источника нечего.
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

/** Итог группы: состояние присылает сервер, здесь только подпись (FR-024e). */
function groupBadge(group: GroupCoverageDto): Badge {
  return STATE_BADGE[group.state];
}

function sourceBadge(source: SourceCoverageDto): Badge {
  return STATE_BADGE[source.state];
}

/**
 * Одна строка факта под состоянием: последняя причина с датой, а если причины
 * нет — сколько не хватает. Имя источника — только когда их в группе больше
 * одного: иначе оно повторяет название группы.
 */
function factOf(group: GroupCoverageDto): string {
  const state = group.state;
  if (state === 'complete' || state === 'running') return '';

  const failure = group.latest_failure;
  if (failure && failure.reason && state !== 'missing') {
    const parts = [
      group.sources.length > 1 ? failure.title : null,
      failure.session_date ? shortDay(failure.session_date) : null,
      failure.reason,
    ];
    return parts.filter((part) => part !== null).join(' · ');
  }
  if (group.has_history && (group.gaps ?? 0) > 0) {
    const gaps = group.gaps ?? 0;
    return `не хватает ${gaps} ${plural(gaps, 'сессии', 'сессий', 'сессий')}`;
  }
  return '';
}

function volumeOf(group: GroupCoverageDto): string {
  if (group.has_history) return `${group.sessions_covered} / ${group.window_sessions}`;
  const ok = group.sources.filter((source) => sourceBadge(source)[0] === 'complete').length;
  return `${ok} / ${group.sources.length}`;
}

/** Дата данных группы; у справочника — дата последней проверки. */
function lastOf(group: GroupCoverageDto): string | null {
  if (group.has_history) return group.period_till ?? null;
  const checked = group.sources
    .map((source) => source.last_checked_at)
    .filter((value): value is string => typeof value === 'string')
    .sort();
  return checked.at(-1)?.slice(0, 10) ?? null;
}

function checkedOf(source: SourceCoverageDto): string {
  if (source.last_checked_at === null || source.last_checked_at === undefined) return '—';
  return `проверено ${formatIsoDate(source.last_checked_at.slice(0, 10))}`;
}

function shortDay(isoDate: string): string {
  return formatIsoDate(isoDate).slice(0, 5);
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
  // Группа собирается и видна, но во вход модели не идёт (FR-060c).
  if (group.model_input === false) return `${count} · пока не входит в модель`;
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
 * Название с заглавной буквы.
 *
 * Сервер возвращает «котировки», артефакт подписывает «Котировки». Своей
 * таблицы названий во фронтенде нет намеренно: она стала бы вторым источником
 * истины и разошлась бы при добавлении группы (research.md R6).
 */
export function capitalize(title: string): string {
  return title.charAt(0).toUpperCase() + title.slice(1);
}
