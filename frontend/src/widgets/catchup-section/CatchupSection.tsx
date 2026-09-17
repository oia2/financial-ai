/**
 * Сбор рыночных данных: ход работы и её итог.
 *
 * Перенесено из артефакта Open Design `market-data.html` (spec 008).
 *
 * Правила, которые здесь важнее вёрстки:
 *
 *  - **панель не исчезает, когда прогон кончился.** На её месте остаётся итог и
 *    журнал: ответ на вопрос «как прошло» не должен пропадать вместе с
 *    процессом (FR-025);
 *  - **пауза автосбора — не состояние прогона.** Начатый прогон она не
 *    обрывает, но о ней обязано знать расписание;
 *  - **остановка показывается состоянием, а не уведомлением**: текущая сессия
 *    доводится до конца, и это видно (FR-032 фичи 005);
 *  - причина прерывания берётся из ответа сервера, а не формулируется здесь.
 */

import type { CatchupStateDto, RunSummaryDto } from '@/entities/market-data';
import { formatIsoDate, formatShortStamp } from '@/shared/lib/market-format';

import { RunJournal } from './RunJournal';
import { SessionProgress } from './SessionProgress';
import { SourceRail } from './SourceRail';

const PAST_WORD: Record<string, string> = {
  finished: 'закончен',
  stopped: 'остановлен',
  failed: 'прерван',
  interrupted: 'прерван перезапуском',
  idle: 'закончен',
};

export function CatchupSection({
  state,
  runs,
  paused,
  nextSession,
  emptyStorage,
  nothingToCatchUp,
  notice,
  onStart,
  onStop,
  onRepeat,
}: {
  state: CatchupStateDto;
  runs: RunSummaryDto[];
  /** Пауза автосбора. Состояние страницы, а не прогона. */
  paused: boolean;
  /** Сессия, которую возьмёт следующий сбор, по торговому календарю. */
  nextSession: string | null;
  emptyStorage: boolean;
  /** Сервер ответил, что пропущенных сессий нет: запуск не предлагается (FR-038). */
  nothingToCatchUp: boolean;
  notice: React.ReactNode;
  onStart: () => void;
  onStop: () => void;
  onRepeat: () => void;
}) {
  const running = state.status === 'running' || state.status === 'stopping';
  const past = !running;
  const stopping = state.status === 'stopping' || state.stop_requested;

  // Прогонов ещё не было: единственный случай, когда панели нечего показать.
  if (past && state.sessions.requested === 0 && runs.length === 0) {
    return (
      <>
        {notice}
        <div className="run-quiet" data-od-id="run-empty">
          <span className="status-dot neutral" />
          <strong>{nothingToCatchUp ? 'Догонять нечего' : 'Прогонов ещё не было'}</strong>
          <span className="quiet">
            {emptyStorage
              ? 'Первичную загрузку выполняет администратор системы.'
              : 'Первый сбор начнётся после закрытия ближайшей сессии, и здесь появится его итог.'}
          </span>
          {!emptyStorage && !nothingToCatchUp && (
            <button className="primary-button run-quiet-action" type="button" onClick={onStart}>
              Ручной сбор
            </button>
          )}
        </div>
      </>
    );
  }

  const mode = state.mode === 'manual' ? 'Ручной сбор' : 'Автосбор';
  const range =
    state.date_from === null
      ? ''
      : state.date_from === state.date_till
        ? formatIsoDate(state.date_from)
        : `${formatIsoDate(state.date_from)} — ${formatIsoDate(state.date_till)}`;

  const title = running
    ? state.current === null
      ? 'Собираем сессии'
      : `Собираем сессию ${formatIsoDate(state.current.session_date)}`
    : `Прогон ${PAST_WORD[state.status] ?? 'закончен'}${
        state.finished_at === null ? '' : ` в ${formatShortStamp(state.finished_at)}`
      }`;

  return (
    <section
      className="collection-panel"
      aria-labelledby="runTitle"
      data-od-id="current-collection"
    >
      <div className="panel-top">
        <div>
          <div className="section-kicker">
            <span
              className={`status-dot${state.status === 'failed' ? ' error' : past ? ' neutral' : ''}`}
            />
            <span>
              {mode}
              {past && ` · ${PAST_WORD[state.status] ?? 'закончен'}`}
              {stopping && running && ' · останавливается'}
            </span>
            {range !== '' && (
              <>
                <span className="quiet">·</span>
                <span className="mono">{range}</span>
              </>
            )}
          </div>
          <h2 id="runTitle">{title}</h2>
        </div>

        {past ? (
          <div className="panel-actions">
            {nothingToCatchUp ? (
              <span className="quiet">Догонять нечего</span>
            ) : (
              <>
                {state.sessions.failed + state.sessions.skipped > 0 && (
                  <button className="secondary-button" type="button" onClick={onRepeat}>
                    Повторить несобранное
                  </button>
                )}
                <button className="primary-button" type="button" onClick={onStart}>
                  Ручной сбор
                </button>
              </>
            )}
          </div>
        ) : stopping ? (
          <button className="secondary-button" type="button" disabled>
            Остановка запрошена
          </button>
        ) : (
          <button className="secondary-button" type="button" onClick={onStop}>
            Остановить прогон
          </button>
        )}
      </div>

      {past && (
        <p className="run-next">
          Следующий сбор —{' '}
          <b>
            {paused
              ? 'не будет'
              : nextSession === null
                ? 'по расписанию'
                : formatIsoDate(nextSession)}
          </b>
          {paused ? ', пока автосбор на паузе' : ', после закрытия сессии'}
        </p>
      )}

      {paused && (
        <div className="run-notice">
          <div>
            <strong>Автосбор на паузе</strong>
            <p>
              {running
                ? 'Начатый прогон пауза не обрывает: он дойдёт до конца. Новых прогонов не будет, пока пауза не снята.'
                : 'Новые сессии собираться не будут, пока пауза не снята. Разрыв растёт по одной сессии в день.'}
            </p>
          </div>
        </div>
      )}

      {stopping && running && (
        <div className="run-notice">
          <div>
            <strong>Останавливается</strong>
            <p>
              Текущая сессия доводится до конца, следующая не начнётся: день, собранный наполовину,
              неотличим от собранного полностью.
            </p>
          </div>
        </div>
      )}

      {state.reason !== null && (
        <div className="run-notice error">
          <div>
            <strong>Причина остановки</strong>
            <p>{state.reason}. Собранное до этого момента сохранено.</p>
          </div>
        </div>
      )}

      {notice}

      <div className={`run-grid${state.sessions.requested <= 1 ? ' single' : ''}`}>
        <SessionProgress sessions={state.sessions} skips={state.skips} />

        {state.sessions.requested > 1 && (
          <div className="source-progress">
            <div className="metric-heading">
              <span>{running ? 'Текущая сессия' : 'Последняя сессия прогона'}</span>
              <strong className="mono">
                {state.current === null ? '—' : formatIsoDate(state.current.session_date)}
              </strong>
            </div>
            <p className="small-note" style={{ marginTop: 12 }}>
              {running
                ? `дальше ещё ${state.sessions.pending} · ${state.sessions.skipped} пропущены с причинами`
                : state.sessions.failed + state.sessions.skipped > 0
                  ? `не собрано: ${state.sessions.failed + state.sessions.skipped}`
                  : 'все сессии прогона собраны'}
            </p>
          </div>
        )}
      </div>

      {state.current !== null && (
        <SourceRail
          sessionDate={state.current.session_date}
          sources={state.current.sources}
          running={running}
        />
      )}

      <div className="run-footline">
        {state.started_at !== null && (
          <div>
            <span>Начало</span>
            <b>{formatShortStamp(state.started_at)}</b>
          </div>
        )}
        {state.finished_at !== null && (
          <div>
            <span>Конец</span>
            <b>{formatShortStamp(state.finished_at)}</b>
          </div>
        )}
        {running && state.last_response_at !== null && (
          <div>
            <span>Последний ответ источника</span>
            <b>{formatShortStamp(state.last_response_at)}</b>
          </div>
        )}
      </div>

      {past && <RunJournal runs={runs} />}
    </section>
  );
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
