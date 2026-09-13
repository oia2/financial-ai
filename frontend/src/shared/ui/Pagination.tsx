/**
 * Страницы длинного списка.
 *
 * Разметка перенесена из артефакта Open Design: она одинакова у истории
 * прогонов, у результата ранжирования и у плана портфеля, и трёх копий одного
 * блока в проекте быть не должно.
 *
 * Идентификатор разметки (`data-od-id`) задаётся вызывающим: в артефакте у
 * каждого из трёх мест он свой, и сверка ведётся именно по нему.
 */

const PAGE_SIZES = [10, 25, 50];

export function Pagination({
  page,
  pageCount,
  pageSize,
  label,
  odId,
  pageSizeOdId,
  prevOdId,
  nextOdId,
  sizes = PAGE_SIZES,
  onPageChange,
  onPageSizeChange,
}: {
  /** Номер страницы, начиная с 1. */
  page: number;
  pageCount: number;
  pageSize: number;
  label: string;
  odId: string;
  pageSizeOdId: string;
  prevOdId: string;
  nextOdId: string;
  sizes?: number[];
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}) {
  return (
    <nav className="pagination" data-od-id={odId} aria-label={label}>
      <div className="pagination-inner">
        <label className="page-size-control">
          <span>Строк:</span>
          <select
            className="page-size-select"
            data-od-id={pageSizeOdId}
            aria-label="Количество строк на странице"
            value={pageSize}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
          >
            {sizes.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>

        <span
          className="pagination-page"
          aria-live="polite"
          aria-label={`Страница ${page} из ${pageCount}`}
        >
          {page} / {pageCount}
        </span>

        <div className="pagination-actions">
          <button
            className="pagination-button"
            type="button"
            aria-label="Предыдущая страница"
            data-od-id={prevOdId}
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m15 18-6-6 6-6" />
            </svg>
          </button>
          <button
            className="pagination-button"
            type="button"
            aria-label="Следующая страница"
            data-od-id={nextOdId}
            disabled={page >= pageCount}
            onClick={() => onPageChange(page + 1)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 18 6-6-6-6" />
            </svg>
          </button>
        </div>
      </div>
    </nav>
  );
}
