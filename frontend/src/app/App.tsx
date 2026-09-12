import { useCatchupState } from '@/entities/market-data';
import { usePortfolioQuery } from '@/entities/portfolio';
import { MarketDataPage } from '@/pages/market-data/MarketDataPage';
import { PortfolioPage } from '@/pages/portfolio/PortfolioPage';
import { ToastHost } from '@/shared/ui/toast/ToastHost';
import { AppHeader } from '@/widgets/app-header/AppHeader';
import { ProcessRail } from '@/widgets/process-rail/ProcessRail';

import { AppProviders } from './providers';
import { useRoute } from './router';
import './styles/design.css';
import './styles/app.css';

export function App() {
  return (
    <AppProviders>
      <ToastHost>
        <AppShell />
      </ToastHost>
    </AppProviders>
  );
}

/**
 * Общая оболочка разделов.
 *
 * Шапка принадлежит оболочке, а не странице: разделов стало два, и состав
 * действий справа в них одинаков (FR-003). Данные портфеля читает здесь же —
 * шапка показывает счёт и интервал в обоих разделах, а запрос в кэше один и
 * тот же, сколько бы подписчиков у него ни было.
 *
 * **Состояние прогона читает тоже оболочка**, а не раздел рыночных данных.
 * Иначе опрос гаснет при переходе в портфель, и баннер живёт на устаревших
 * числах — а FR-006 требует, чтобы идущий сбор был виден из любого раздела.
 */
export function AppShell() {
  const route = useRoute();
  const portfolio = usePortfolioQuery();
  const catchup = useCatchupState();

  return (
    <div className="app-shell">
      <AppHeader data={portfolio.data} />

      <ProcessRail state={catchup.data} route={route} />

      {route === 'market-data' ? <MarketDataPage /> : <PortfolioPage />}
    </div>
  );
}
