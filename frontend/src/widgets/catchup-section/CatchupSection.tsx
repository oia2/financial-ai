/**
 * Управление догоном и ход прогона.
 *
 * Шесть состояний одним компонентом, как в артефакте Open Design: у прогона
 * один язык, и переключение между состояниями не должно выглядеть переходом
 * между разными экранами.
 *
 * Правила, которые здесь важнее верстки:
 *
 *  - в `stopped` интерфейс переходит **по ответу сервера**, а не по факту
 *    нажатия (FR-032);
 *  - у `stopping` нет ни повторной остановки, ни возобновления (FR-024);
 *  - `finished` с ненулевым `failed` не показывается как успех (FR-033);
 *  - причина прерывания берётся из ответа, а не формулируется здесь (FR-034).
 */

import type { CatchupStateDto, CoverageDto } from '@/entities/market-data';
import { formatIsoDate, formatMoscowStamp } from '@/shared/lib/market-format';

import { ProgressTrack } from './ProgressTrack';

const LABELS: Record<CatchupStateDto['status'], string> = {
  idle: 'Не запущен',
  running: 'Идёт сбор',
  stopping: 'Останавливается',
  stopped: 'Остановлен',
  // Артефакт подписывает это состояние «Диапазон завершён». Владелец проекта
  // 2026-09-10 решил в пользу «Догон завершён»: смысл тот же, слово ближе к
  // тому, чем человек управлял. Подпись безопасна потому, что при незакрытых
  // сессиях состояние называется иначе — «Есть незакрытые сессии», — и «Догон
  // завершён» никогда не показывается поверх пропусков (FR-033).
  finished: 'Догон завершён',
  failed: 'Прерван ошибкой',
};

export function CatchupSection({
  state,
  coverage,
  emptyStorage,
  nothingToCatchUp,
  notice,
  onStart,
  onStop,
  onResume,
}: {
  state: CatchupStateDto;
  coverage: CoverageDto | undefined;
  /** Хранилище пусто: нужна первичная загрузка, догон недоступен (FR-026). */
  emptyStorage: boolean;
  /** Сервер ответил, что пропущенных сессий нет: новый прогон не предлагается (FR-038). */
  nothingToCatchUp: boolean;
  /** Сообщение поверх состояния: отказ запуска, объяснение idle после перезапуска. */
  notice: React.ReactNode;
  onStart: () => void;
  onStop: () => void;
  onResume: () => void;
}) {
  const disabled = emptyStorage || nothingToCatchUp;
  const stopping = state.status === 'stopping';
  const partial = state.status === 'finished' && state.failed > 0;
  const busy = state.status === 'running' || stopping;
  const unclosed = state.remaining + state.failed;

  const label = partial ? 'Есть незакрытые сессии' : LABELS[state.status];
  const tone =
    state.status === 'failed' || partial ? 'failed' : state.status === 'finished' ? 'finished' : '';

  return (
    <section className="catchup-section" aria-labelledby="catchupHeading">
      <div className="catchup-top">
        <div>
          <h2 id="catchupHeading">Догон истории</h2>
          <p>{description(state, partial, emptyStorage, nothingToCatchUp)}</p>
        </div>

        <div className="catchup-actions">
          <span className={`status-tag ${tone}`.trim()}>
            {busy && <ProcessGlyph stopping={stopping} />}
            {label}
          </span>

          {state.status === 'running' && (
            <button className="secondary-button" type="button" onClick={onStop}>
              Остановить
            </button>
          )}

          {/* У `stopping` действий нет: запрос уже доведёт сессию до конца. */}
          {stopping && <span className="status-tag">Остановка запрошена</span>}

          {!busy &&
            (canResume(state, partial, unclosed) ? (
              <button className="primary-button" type="button" onClick={onResume}>
                Продолжить догон
              </button>
            ) : (
              !disabled && (
                <button className="primary-button" type="button" onClick={onStart}>
                  Настроить запуск
                </button>
              )
            ))}
        </div>
      </div>

      {notice}

      {state.requested > 0 ? (
        <div className="run-body">
          <div className="run-figures">
            <div className="run-figure">
              <small>Закрыто сессий</small>
              <strong>
                {state.closed} <span>из {state.requested}</span>
              </strong>
            </div>
            <div className="run-figure">
              <small>Осталось обработать</small>
              <strong>{state.remaining}</strong>
            </div>
            <div className={`run-figure${state.failed > 0 ? ' has-errors' : ''}`}>
              <small>Не закрыто</small>
              <strong>{state.failed}</strong>
            </div>
          </div>

          <ProgressTrack
            requested={state.requested}
            closed={state.closed}
            failed={state.failed}
            remaining={state.remaining}
            finished={state.status === 'finished' && !partial}
          />

          {busy && state.current !== null && (
            <div className="current-session">
              <ProcessGlyph stopping={stopping} />
              <span>{stopping ? 'Завершаем сессию' : 'Сейчас обрабатывается'}</span>
              <time dateTime={state.current}>{formatIsoDate(state.current)}</time>
              <span className="session-note">
                {state.groups.includes('positions') ? (
                  <>
                    Позиции по фьючерсам — около <span className="mono">2,5 мин</span> на сессию
                  </>
                ) : (
                  <>
                    Котировки — <span className="mono">1–2 с</span> на сессию
                  </>
                )}
              </span>
            </div>
          )}

          {stopping && (
            <RunNotice title="Дожидаемся завершения сессии">
              Остановка может занять несколько минут. Это нужно, чтобы не оставить день собранным
              наполовину. Можно перейти в портфель — состояние останется в шапке.
            </RunNotice>
          )}

          {state.status === 'failed' && state.reason !== null && (
            <RunNotice title="Причина остановки" error>
              {state.reason}
            </RunNotice>
          )}

          {partial && (
            <RunNotice title="Диапазон завершён с пропусками" error>
              Не закрыто <span className="mono">{state.failed}</span>. Продолжение повторно
              запланирует незакрытые сессии; уже закрытые собираться не будут.
            </RunNotice>
          )}

          {state.status === 'stopped' && (
            <RunNotice title="Можно продолжить с оставшихся сессий">
              Повторный запуск заново проверит пропуски. Уже закрытые сессии не будут собираться
              повторно.
            </RunNotice>
          )}

          <div className="run-meta">
            <span className="groups-label">
              {groupsLabel(state, coverage)} ·{' '}
              <span className="mono">
                {formatIsoDate(state.date_from)} — {formatIsoDate(state.date_till)}
              </span>
            </span>
            <span>
              Начало: <span className="mono">{formatMoscowStamp(state.started_at)}</span>
              {state.finished_at !== null && (
                <>
                  {' · '}Конец: <span className="mono">{formatMoscowStamp(state.finished_at)}</span>
                </>
              )}
            </span>
          </div>
        </div>
      ) : (
        notice === null && (
          <div className="idle-note">
            <p>
              По умолчанию — все группы и всё окно. Собираются только пропуски; уже закрытые сессии
              остаются на месте.
            </p>
          </div>
        )
      )}
    </section>
  );
}

/**
 * Продолжение доступно там, где осталось что собирать.
 *
 * Это новый запуск: перечислять незакрытые сессии интерфейс не должен —
 * сервер заново вычислит пропуски сам (FR-025).
 */
function canResume(state: CatchupStateDto, partial: boolean, unclosed: number): boolean {
  const finishedWithWork = state.status === 'stopped' || state.status === 'failed' || partial;
  return finishedWithWork && unclosed > 0;
}

function description(
  state: CatchupStateDto,
  partial: boolean,
  emptyStorage: boolean,
  nothingToCatchUp: boolean,
): string {
  if (emptyStorage) return 'Нужна первичная загрузка';
  if (nothingToCatchUp) return 'Догонять нечего';
  if (state.status === 'stopping') return 'Завершаем текущую сессию. Следующая не начнётся.';
  if (state.status === 'stopped') return 'Остановлен по вашей команде. Закрытые сессии сохранены.';
  if (state.status === 'finished') {
    return partial
      ? 'Диапазон пройден, но часть сессий не закрылась.'
      : 'Все запрошенные сессии закрыты. Наполненность проверяйте по сводке.';
  }
  if (state.status === 'failed') return 'Сбор прерван. Уже закрытые сессии сохранены.';
  return 'Собирает пропущенные сессии в доступном окне.';
}

/** Названия групп берутся из сводки: своей таблицы меток нет (research.md R6). */
function groupsLabel(state: CatchupStateDto, coverage: CoverageDto | undefined): string {
  const total = coverage?.groups.length ?? 5;
  if (state.groups.length === 0 || state.groups.length === total) return 'Все группы';

  return state.groups
    .map((id) => {
      const title = coverage?.groups.find((row) => row.group === id)?.title ?? id;
      return title.charAt(0).toUpperCase() + title.slice(1);
    })
    .join(', ');
}

export function RunNotice({
  title,
  error,
  children,
}: {
  title: string;
  error?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`run-notice${error === true ? ' error' : ''}`}>
      <div>
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
    </div>
  );
}

/**
 * Малый индикатор активности.
 *
 * Показывает, что процесс идёт, и только это: процентом готовности текущей
 * сессии он не является и читаться так не должен (FR-057). Цикл дыхания —
 * 3 с, при остановке 4,8 с; задаётся CSS артефакта.
 */
function ProcessGlyph({ stopping }: { stopping: boolean }) {
  return (
    <span className={`process-glyph${stopping ? ' is-stopping' : ''}`} aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
