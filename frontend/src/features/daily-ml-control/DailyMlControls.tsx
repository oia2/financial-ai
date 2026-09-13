/**
 * Кнопки управления в шапке раздела.
 *
 * Подпись «Проверить сейчас» выбрана вместо «Запустить»: запуска по требованию
 * в этой системе нет, есть проверка готовности (FR-022, FR-051).
 *
 * Прежняя подпись «Проверить новые данные» отвергнута — она обещала то, чего
 * кнопка не делает. К бирже действие не обращается вовсе: оно смотрит на уже
 * собранное и решает, есть ли работа ранжированию. Человек читал её как «сходи
 * посмотри, не появилось ли данных», а при разрыве данных получал «готовых
 * данных нет» и никакого движения — чинится это в разделе рыночных данных, куда
 * и ведёт ссылка в состоянии `data_gap`.
 */

export function DailyMlControls({
  paused,
  busy,
  onTogglePause,
  onCheckNow,
}: {
  paused: boolean;
  busy: boolean;
  onTogglePause: () => void;
  onCheckNow: () => void;
}) {
  return (
    <div className="ml-controls" data-od-id="ranking-controls">
      <div className="ml-automation-actions">
        <button
          className="secondary-button"
          type="button"
          aria-pressed={paused}
          aria-label={
            paused
              ? 'Запустить автоматическое ранжирование'
              : 'Остановить автоматическое ранжирование'
          }
          data-od-id="pause-ranking"
          disabled={busy}
          onClick={onTogglePause}
        >
          {paused ? 'Запустить' : 'Стоп'}
        </button>
        <button
          className="secondary-button"
          type="button"
          data-od-id="check-ranking-now"
          disabled={busy}
          onClick={onCheckNow}
        >
          Проверить сейчас
        </button>
      </div>
    </div>
  );
}
