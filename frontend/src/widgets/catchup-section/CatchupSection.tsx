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

import type { CatchupStateDto, LinkEventDto, RunsDto, RunSummaryDto } from '@/entities/market-data';
import {
  formatAgo,
  formatDuration,
  formatIsoDate,
  formatShortStamp,
} from '@/shared/lib/market-format';

import { EventLog } from './EventLog';
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
  events = [],
  eventsTotal = 0,
  skips = [],
  journalSkipsTotal = 0,
  paused,
  nextSession,
  nextBlocked = false,
  emptyStorage,
  nothingToCatchUp,
  notice,
  stale = false,
  onStart,
  onStop,
  offerContinue,
  onDiscard,
  onRepeat,
}: {
  state: CatchupStateDto;
  runs: RunSummaryDto[];
  /** Изменения состава инструментов из журнала. */
  events?: LinkEventDto[];
  eventsTotal?: number;
  /** Причины пропусков из хранилища: они переживают перезапуск сборщика. */
  skips?: RunsDto['skips'];
  journalSkipsTotal?: number;
  /** Пауза автосбора. Состояние страницы, а не прогона. */
  paused: boolean;
  /** Сессия, которую возьмёт следующий сбор, по торговому календарю. */
  nextSession: string | null;
  /**
   * Даты нет потому, что сбор не возьмёт НИЧЕГО: недостающие сессии исчерпали
   * попытки. Пустая дата без этого признака значит обратное — недостающего
   * нет, и сбор пойдёт по расписанию. Одна подпись на оба случая читалась как
   * обещание там, где обещания нет (FR-054).
   */
  nextBlocked?: boolean;
  emptyStorage: boolean;
  /** Сервер ответил, что пропущенных сессий нет: запуск не предлагается (FR-038). */
  nothingToCatchUp: boolean;
  notice: React.ReactNode;
  /**
   * Связи со сборщиком нет.
   *
   * Панель заменяется одной строкой — так в макете. Счётчики и ленты при
   * потерянной связи показывали бы идущий сбор, которого, может быть, уже нет
   * (contracts/ui-states.md: «вида, будто сбор идёт»).
   */
  stale?: boolean;
  onStart: () => void;
  onStop: () => void;
  /**
   * Предложить выбор после остановки.
   *
   * Решает страница, а не панель: отказ от предложения — состояние экрана, а
   * не сборщика. Прогон остановлен в любом случае, вопрос лишь в том, показывать
   * ли ещё выбор «продолжить или бросить».
   */
  offerContinue: boolean;
  /** Отказаться от остановленного прогона: непройденное остаётся непройденным. */
  onDiscard: () => void;
  onRepeat: () => void;
}) {
  if (stale) {
    return (
      <>
        {notice}
        <div className="run-quiet" data-od-id="run-unavailable">
          <span className="status-dot error" />
          <strong>Сборщик недоступен</strong>
          <span className="quiet">
            Сервер отвечает, а сборщик рыночных данных — нет. Ниже показано последнее известное
            состояние: оно не означает, что сбор идёт.
          </span>
        </div>
      </>
    );
  }

  const running = state.status === 'running' || state.status === 'stopping';
  const past = !running;
  const stopping = state.status === 'stopping' || state.stop_requested;

  /*
    Разрыв больше предела — не ошибка прогона, а отказ автосбора брать работу:
    сам прогон прошёл нормально. Причина и числа приходят с сервера, интерфейс
    их не выводит (FR-002).
  */
  const blockedByGap = state.skips.find((skip) => skip.reason === 'gap_over_limit');

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

        {past && offerContinue ? (
          // Остановка — не отмена. Прогон прерван по команде, непройденные
          // сессии никуда не делись, и человек выбирает: доводить или бросить.
          // Молчаливый переход к «начать заново» этот выбор стирал бы.
          <div className="panel-actions">
            <button className="secondary-button" type="button" onClick={onDiscard}>
              Отменить прогон
            </button>
            <button className="primary-button" type="button" onClick={onRepeat}>
              Продолжить прогон
            </button>
          </div>
        ) : past ? (
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
          Следующий сбор — <b>{paused || nextBlocked ? 'не будет' : formatIsoDate(nextSession)}</b>
          {paused
            ? ', пока автосбор на паузе'
            : nextBlocked
              ? ', недостающие сессии исчерпали попытки — нужен ручной сбор'
              : ', после закрытия сессии'}
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
              Идущий источник доводится до конца, следующий не начнётся. Собранное сохранено;
              недобранные источники остаются в плане и добираются следующим прогоном.
            </p>
          </div>
        </div>
      )}

      {blockedByGap !== undefined && (
        <div className="run-notice error">
          <div>
            <strong>Автосбор не берёт этот разрыв</strong>
            <p>
              {blockedByGap.detail ?? 'Разрыв больше предела'}. Лавина обращений к бирже без спроса
              запрещена, поэтому разрыв закрывается ручным сбором — можно частями.
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
        <SessionProgress
          sessions={state.sessions}
          skips={state.skips}
          skipsTotal={state.skips_total}
          current={state.current}
        />

        {state.sessions.requested > 1 && (
          <div className="source-progress">
            <div className="metric-heading">
              <span>{running ? 'Текущая сессия' : 'Последняя сессия прогона'}</span>
              <strong className="mono">
                {state.current === null ? '—' : formatIsoDate(state.current.session_date)}
              </strong>
            </div>
            <p className="small-note" style={{ marginTop: 12 }}>
              {/*
                Непройденные сессии — тоже несобранные. У остановленного
                прогона они и составляют остаток: сказать про него «все сессии
                собраны» значило бы объявить собранным то, к чему даже не
                приступали.
              */}
              {running
                ? `дальше ещё ${state.sessions.pending} · ${state.sessions.skipped} пропущены с причинами`
                : unfinishedNote(state.sessions)}
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
        {past && state.started_at !== null && state.finished_at !== null && (
          <div>
            <span>Длительность</span>
            <b>{formatDuration(state.started_at, state.finished_at)}</b>
          </div>
        )}
        {running && state.last_response_at !== null && (
          <div>
            <span>Последний ответ источника</span>
            <b>{formatAgo(state.last_response_at)}</b>
            <span className="msk">· {formatShortStamp(state.last_response_at)}</span>
          </div>
        )}
        {running && state.started_at !== null && (
          <div>
            {/* Сколько прогон ИДЁТ — факт. Сколько ещё продлится, раздел не
                обещает: оценка вычислялась бы по прошлым прогонам (FR-027). */}
            <span>Идёт</span>
            <b>{formatDuration(state.started_at, null)}</b>
          </div>
        )}
      </div>

      {/*
        Журнал событий остаётся и после прогона: последними в нём стоят как раз
        остановка и завершение, и прятать их в тот же миг, когда они случились,
        значит не показать их никогда. Он живёт в памяти сборщика и исчезает с
        перезапуском — в отличие от журнала прогонов ниже.
      */}
      <EventLog log={state.log} />

      {past && (
        <RunJournal
          runs={runs}
          events={events}
          eventsTotal={eventsTotal}
          skips={skips}
          skipsTotal={journalSkipsTotal}
        />
      )}
    </section>
  );
}

/**
 * Что осталось несобранным у прошедшего прогона.
 *
 * Три разных случая, и смешивать их нельзя: сессия могла не собраться, могла
 * быть пропущена с причиной, а могла вовсе не начинаться — последнее и есть
 * остаток остановленного прогона. Прежде подпись говорила «все сессии прогона
 * собраны» про то, к чему не приступали.
 */
function unfinishedNote(sessions: CatchupStateDto['sessions']): string {
  const parts = [
    sessions.failed > 0 ? `не собрано: ${sessions.failed}` : '',
    sessions.skipped > 0 ? `пропущено: ${sessions.skipped}` : '',
    sessions.pending > 0 ? `не начинались: ${sessions.pending}` : '',
  ].filter(Boolean);

  return parts.length > 0 ? parts.join(' · ') : 'все сессии прогона собраны';
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
