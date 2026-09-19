/**
 * Раздел «Рыночные данные».
 *
 * Композиция перенесена из артефакта Open Design (`market-data.html`):
 * заголовок с датой снимка и кнопкой запуска, уведомление о расхождении,
 * сообщение о суженном диапазоне, сводка полноты, управление догоном.
 *
 * Раздел не привязан к брокерскому счёту: рыночные данные общие, и смена или
 * отсутствие счёта их не меняет (FR-005).
 *
 * Состояние прогона страница **читает из кэша**, а не заводит своё: владелец
 * запроса — оболочка приложения, иначе баннер процесса гас бы при переходе в
 * портфель (FR-006, research.md R5).
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import {
  catchupQueryKey,
  coverageQueryKey,
  isCatchupActive,
  useCatchupState,
  useCollectionSettings,
  useCoverage,
  useRuns,
  useSetCollectionPaused,
  type GroupCoverageDto,
} from '@/entities/market-data';
import { ClampNotice } from '@/features/catchup-launch/ClampNotice';
import { LaunchDrawer } from '@/features/catchup-launch/LaunchDrawer';
import { useCatchupControl } from '@/features/catchup-launch/useCatchupControl';
import { ApiError, ServerUnreachableError } from '@/shared/api/client';
import { formatIsoDate } from '@/shared/lib/market-format';
import { CatchupSection, RunNotice } from '@/widgets/catchup-section/CatchupSection';
import { CollectionCalendar } from '@/widgets/collection-calendar/CollectionCalendar';
import { EmptyValuesAlert } from '@/widgets/completeness-table/EmptyValuesAlert';
import { GroupsSection } from '@/widgets/completeness-table/GroupsSection';
import { GroupDetailsDrawer } from '@/widgets/completeness-table/GroupDetailsDrawer';

/**
 * Биржевой порог в поясе зрителя.
 *
 * Время показывается местное, московское — только у порога и только как
 * дополнение: это биржевое правило, а не наше (FR-022).
 */
/**
 * Город зрителя по его часовому поясу.
 *
 * Берётся у браузера, а не спрашивается и не хранится: пояс — свойство того,
 * кто смотрит, и второе его объявление разошлось бы с первым.
 */
function viewerZone(): string {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return zone.split('/').pop()?.replace(/_/g, ' ') ?? zone;
}

function localThreshold(exchangeTime: string): string {
  const [hours = 19, minutes = 30] = exchangeTime.split(':').map(Number);
  const moscow = new Date(Date.UTC(2026, 0, 1, hours - 3, minutes));
  return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(moscow);
}

export function MarketDataPage() {
  const coverage = useCoverage();
  const catchup = useCatchupState();
  const control = useCatchupControl();
  const collection = useCollectionSettings();
  const runs = useRuns();
  const setCollectionPaused = useSetCollectionPaused();
  const queryClient = useQueryClient();
  const [details, setDetails] = useState<GroupCoverageDto | null>(null);

  /*
    От какого остановленного прогона человек уже отказался.
    Опознаётся моментом начала: это состояние ЭКРАНА, а не сборщика — прогон
    остановлен в любом случае, вопрос лишь в том, предлагать ли ещё выбор.
  */
  const [discarded, setDiscarded] = useState<string | null>(null);

  // Пока состояние не прочитано, сбор считается идущим: это умолчание сборщика,
  // и мигать надписью «остановлено» на первом кадре незачем.
  const collectionPaused = collection.data?.paused ?? false;

  // Пустое хранилище — состояние системы, а не авария: сборщик отвечает
  // `calendar_empty`, пока не выполнена первичная загрузка (FR-026).
  const emptyStorage =
    coverage.error instanceof ApiError && coverage.error.code === 'calendar_empty';
  const disconnected = coverage.error instanceof ServerUnreachableError;
  const workerDown =
    (coverage.error instanceof ApiError && coverage.error.code === 'worker_unavailable') ||
    (catchup.error instanceof ApiError && catchup.error.code === 'worker_unavailable');

  /**
   * Состояние прогона перечитывается при входе в раздел (FR-035).
   *
   * Владелец запроса — оболочка, и она не размонтируется при переходе между
   * разделами; при неактивном прогоне опрос выключен. Без этого чтения человек,
   * вернувшийся в раздел через полчаса, видел бы состояние получасовой
   * давности.
   */
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: catchupQueryKey });
  }, [queryClient]);

  /**
   * Прогон закончился — сводка перечитывается.
   *
   * FR-037 запрещает **выводить** сводку из статуса прогона: `finished` сам по
   * себе не означает стопроцентного покрытия, и рисовать его нельзя. Но
   * заставлять человека жать «Обновить», чтобы увидеть результат только что
   * завершённого сбора, — не то же самое: числа берутся из настоящего чтения
   * с сервера, просто оно выполняется само.
   */
  const catchupStatus = catchup.data?.status;
  const wasActive = useRef(false);
  useEffect(() => {
    const active = catchupStatus !== undefined && isCatchupActive(catchupStatus);

    if (wasActive.current && !active) {
      void queryClient.invalidateQueries({ queryKey: coverageQueryKey });
    }
    wasActive.current = active;
  }, [catchupStatus, queryClient]);

  /**
   * Признак «догонять нечего» снимается вместе с перечитанной сводкой.
   *
   * Иначе выход из этого состояния был бы только через перезагрузку страницы:
   * кнопка запуска скрыта, а пропуски могли появиться.
   */
  const coverageUpdatedAt = coverage.dataUpdatedAt;
  const clearNothingToCatchUp = control.clearNothingToCatchUp;
  const firstCoverage = useRef(true);
  useEffect(() => {
    if (firstCoverage.current) {
      firstCoverage.current = false;
      return;
    }
    clearNothingToCatchUp();
  }, [coverageUpdatedAt, clearNothingToCatchUp]);

  const running = catchup.data !== undefined && isCatchupActive(catchup.data.status);

  return (
    <main className="market-main">
      <div className="page-heading market-heading">
        <div>
          <p className="eyebrow">Полнота и сбор истории</p>
          <h1>Рыночные данные</h1>
        </div>
        <div className="heading-actions">
          {coverage.data !== undefined && (
            <span className="snapshot-note">
              Состояние на
              <br />
              <time dateTime={coverage.data.asof_date}>
                {formatIsoDate(coverage.data.asof_date)}
              </time>
            </span>
          )}

          {/*
            Остановка автоматического сбора. Отдельно от паузы ранжирования: это
            разные механизмы, и слитое прочтение дороже прочих ошибок на этих
            экранах (FR-029e). Состояние читается с сервера, а не запоминается
            здесь: оно живёт в процессе сборщика, и перезапуск возвращает сбор.

            Подпись говорит про АВТОСБОР, а не про «сбор»: «Остановить сбор»
            читалось как остановка уже идущего прогона, чем кнопка не является —
            она выключает автоматический режим, а начатую сессию доводит до конца.
          */}
          <button
            className="secondary-button"
            type="button"
            aria-pressed={collectionPaused}
            aria-label={
              collectionPaused
                ? 'Возобновить автоматический сбор данных'
                : 'Поставить автоматический сбор данных на паузу'
            }
            data-od-id="pause-collection"
            disabled={collection.isPending || setCollectionPaused.isPending}
            onClick={() => setCollectionPaused.mutate(!collectionPaused)}
          >
            {collectionPaused ? 'Возобновить автосбор' : 'Пауза автосбора'}
          </button>
        </div>
      </div>

      {/*
        Строка о часовом поясе — из артефакта. Всё время в разделе местное,
        и сказать об этом нужно один раз в начале, а не приписывать к каждому
        числу: московское появляется только у биржевого порога (FR-022).
      */}
      <p className="tz-line">
        <span>
          Время — ваше, <b>{viewerZone()}</b>.
        </span>
      </p>

      {catchup.data !== undefined && (
        <CatchupSection
          stale={workerDown || disconnected}
          state={catchup.data}
          runs={runs.data?.runs ?? []}
          events={runs.data?.events ?? []}
          eventsTotal={runs.data?.events_total ?? 0}
          skips={runs.data?.skips ?? []}
          journalSkipsTotal={runs.data?.skips_total ?? 0}
          paused={collectionPaused}
          nextSession={coverage.data?.next_session ?? null}
          nextBlocked={coverage.data?.next_session_blocked === true}
          nextClosed={coverage.data?.next_session_closed === true}
          emptyStorage={emptyStorage}
          nothingToCatchUp={control.nothingToCatchUp}
          notice={
            control.pageNotice === null ? null : (
              <RunNotice title={control.pageNotice.title} error={control.pageNotice.error}>
                {control.pageNotice.message}
              </RunNotice>
            )
          }
          offerContinue={catchup.data.status === 'stopped' && discarded !== catchup.data.started_at}
          onDiscard={() => setDiscarded(catchup.data?.started_at ?? null)}
          onStart={control.openDrawer}
          onStop={control.requestStop}
          onRepeat={() => control.resume(catchup.data.groups)}
        />
      )}

      {coverage.data !== undefined && <EmptyValuesAlert groups={coverage.data.groups} />}

      {control.clamp !== null && <ClampNotice {...control.clamp} />}

      {emptyStorage ? (
        <EmptyStorage />
      ) : coverage.data !== undefined ? (
        // Сводка остаётся на месте и при потерянной связи: это и есть то
        // «последнее известное состояние», о котором сказала строка выше.
        // Подменять её объяснением значило бы прятать собранное (FR-042).
        <GroupsSection
          asofDate={coverage.data.asof_date}
          groups={coverage.data.groups}
          universe={coverage.data.universe}
          onOpenDetails={setDetails}
        />
      ) : disconnected || workerDown ? (
        <Unreachable offline={disconnected} />
      ) : (
        <div className="empty-summary" aria-live="polite">
          <p>Читаем состояние данных…</p>
        </div>
      )}

      {coverage.data !== undefined && (
        <CollectionCalendar
          nextSession={coverage.data.next_session}
          nextClosed={coverage.data.next_session_closed === true}
          threshold={{
            local: localThreshold(coverage.data.ingest_after_close),
            exchange: `${coverage.data.ingest_after_close} МСК`,
          }}
          lastClosed={coverage.data.asof_date}
          paused={collectionPaused}
          collecting={running ? (catchup.data?.current?.session_date ?? null) : null}
          skipReasons={Object.fromEntries(
            (runs.data?.skips ?? []).map((skip) => [skip.session_date, skip.detail ?? skip.reason]),
          )}
          onCollect={(date) => control.launch({ groups: null, date_from: date, date_till: date })}
        />
      )}

      <LaunchDrawer
        open={control.drawerOpen}
        coverage={coverage.data}
        pending={control.launching}
        error={control.refusal}
        onSubmit={control.launch}
        onClose={control.closeDrawer}
      />

      <GroupDetailsDrawer group={details} onClose={() => setDetails(null)} />
    </main>
  );
}

/**
 * Хранилище пусто.
 *
 * Первичная загрузка остаётся отдельной операцией вне интерфейса, поэтому
 * здесь объяснение и обращение к администратору, а не кнопка (FR-026).
 */
function EmptyStorage() {
  return (
    <div className="empty-summary">
      <h2>Хранилище пока пусто</h2>
      <p>
        Сначала нужна первичная загрузка рыночных данных — её выполняет администратор системы. После
        неё здесь появится сводка покрытия и наполненности.
      </p>
    </div>
  );
}

/**
 * Показать нечего: связи нет и собранного в кэше тоже.
 *
 * Отличается от недоступности при наличии кэша: там сводка остаётся и о ней
 * сказано строкой в панели. Здесь показывать нечего вовсе.
 */
function Unreachable({ offline }: { offline: boolean }) {
  return (
    <div className="empty-summary">
      <h2>{offline ? 'Нет связи с сервером Financial AI' : 'Сборщик данных недоступен'}</h2>
      <p>
        {offline
          ? 'Ответ не получен, и показать пока нечего: сводка ни разу не прочитана.'
          : 'Сервер Financial AI отвечает, а сборщик рыночных данных — нет. Собранного в этом сеансе ещё не читали, поэтому показать нечего.'}
      </p>
    </div>
  );
}
