import { useEffect, useRef, useState } from 'react';

import type { PortfolioDto } from '@/entities/portfolio';
import { RefreshIntervalForm } from '@/features/refresh-interval-setting/RefreshIntervalForm';
import { useRefreshNow } from '@/features/refresh-now/useRefreshNow';

/**
 * Шапка раздела по утверждённому дизайну: марка слева, действия справа.
 *
 * Действия — иконка обновления, ярлык интервала и меню счёта. Меню содержит
 * идентификацию счёта, повтор действия «Обновить данные» и форму интервала:
 * так это устроено в артефакте Open Design, и порядок здесь не переставляется.
 */
export function AppHeader({ data }: { data: PortfolioDto | undefined }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const controlRef = useRef<HTMLDivElement>(null);
  const refresh = useRefreshNow();

  // Меню закрывается кликом вне его — как в прототипе.
  useEffect(() => {
    if (!menuOpen) return;

    function onDocumentClick(event: MouseEvent) {
      if (!controlRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }

    document.addEventListener('click', onDocumentClick);
    return () => document.removeEventListener('click', onDocumentClick);
  }, [menuOpen]);

  const account = data?.broker.account ?? null;
  const interval = data?.sync.refresh_interval_seconds;
  const busy = refresh.isPending || (data?.sync.in_progress ?? false);

  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-name">FINANCIAL AI</span>
      </div>

      <div className="header-actions" ref={controlRef}>
        <button
          className="icon-button"
          type="button"
          aria-label="Обновить данные"
          onClick={() => refresh.run()}
          disabled={busy}
          aria-busy={busy}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 11a8 8 0 0 0-14.9-4M4 4v4h4M4 13a8 8 0 0 0 14.9 4M20 20v-4h-4" />
          </svg>
        </button>

        <button
          className="interval-shortcut"
          type="button"
          aria-label="Настроить интервал автообновления"
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span className="interval-shortcut-label">Авто</span>
          <strong>{interval === undefined ? '—' : `${interval} с`}</strong>
        </button>

        <button
          className="account-button"
          type="button"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="M3 10h18" />
          </svg>
          <span className="account-copy">
            {/* Номер договора — только маскированный (FR-022). */}
            <strong>{account === null ? 'Т-Банк' : `Т-Банк · ${account.masked_id}`}</strong>
            <span>{account?.display_name ?? 'Брокерский счёт'}</span>
          </span>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m8 10 4 4 4-4" />
          </svg>
        </button>

        <div className="account-menu" role="menu" hidden={!menuOpen}>
          <div className="menu-account">
            <strong>{account?.display_name ?? 'Брокерский счёт'}</strong>
            <span>
              {account === null ? 'Счёт не получен' : `Т-Банк · договор ${account.masked_id}`}
            </span>
          </div>

          <button
            className="menu-action"
            type="button"
            role="menuitem"
            onClick={() => refresh.run()}
            disabled={busy}
          >
            Обновить данные
          </button>

          <RefreshIntervalForm />
        </div>
      </div>
    </header>
  );
}
