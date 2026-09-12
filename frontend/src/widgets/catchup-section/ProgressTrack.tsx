/**
 * Индикатор хода прогона.
 *
 * Два сегмента, а не один: закрытые сессии и незакрывшиеся. Смешать их
 * значило бы объявить прогон успешным там, где часть сессий не собралась
 * (FR-030, FR-033).
 *
 * Длина подтверждённой части меняется только при новом `closed` от сервера:
 * никакого движения «пока идёт» интерфейс не придумывает, и оценки времени
 * окончания тоже нет (FR-031). Плавность перехода задаёт CSS артефакта —
 * 700 мс на изменение ширины.
 */

export function ProgressTrack({
  requested,
  closed,
  failed,
  remaining,
  finished,
}: {
  requested: number;
  closed: number;
  failed: number;
  remaining: number;
  finished: boolean;
}) {
  const share = (value: number) => (requested > 0 ? `${(value / requested) * 100}%` : '0%');

  return (
    <div
      className={`progress-track${finished ? ' finished' : ''}`}
      role="progressbar"
      aria-label="Закрытые сессии"
      aria-valuemin={0}
      aria-valuemax={requested}
      aria-valuenow={closed}
      aria-valuetext={`Закрыто ${closed} из ${requested}; не закрыто ${failed}; осталось обработать ${remaining}`}
    >
      <span className="closed-fill" style={{ width: share(closed) }} />
      <span className="failed-fill" style={{ width: share(failed) }} />
    </div>
  );
}
