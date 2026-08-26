import type { SnapshotDto, SyncDto } from '@/entities/portfolio';
import { formatAge, formatDateTime, formatTime } from '@/shared/lib/format';

import './freshness.css';

/**
 * Актуальность данных: возраст и точное время последней успешной
 * синхронизации показываются в каждом состоянии, где видны данные (FR-014).
 */
export function Freshness({ snapshot, sync }: { snapshot: SnapshotDto | null; sync: SyncDto }) {
  const failed = sync.status === 'failed';

  const primary = failed
    ? 'Нет обновления'
    : snapshot === null
      ? '—'
      : formatAge(snapshot.age_seconds);

  const secondary =
    sync.last_success_at === null
      ? 'Синхронизации ещё не было'
      : failed || sync.is_stale
        ? `Последняя успешная синхронизация ${formatTime(sync.last_success_at)}`
        : `Обновлено ${formatDateTime(sync.last_success_at)}`;

  return (
    <div className="freshness">
      <span className="freshness-label muted">Актуальность</span>
      <span className="freshness-value numeric">{primary}</span>
      <span className="freshness-time muted">{secondary}</span>
    </div>
  );
}
