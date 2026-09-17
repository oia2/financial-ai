/**
 * Состояние прогона словом.
 *
 * Слово, а не только цвет: состояние обязано читаться без различения цветов
 * (FR-072). Класс задаёт оформление, текст — смысл.
 */

import type { RunStatus } from '@/entities/daily-ml';

export const RUN_STATUS_NAMES: Record<RunStatus, string> = {
  queued: 'Ожидает',
  running: 'В работе',
  success: 'Завершён',
  failed: 'Отказ',
};

export function RunStatusTag({ status }: { status: RunStatus }) {
  return <span className={`ml-run-status ${status}`}>{RUN_STATUS_NAMES[status]}</span>;
}
