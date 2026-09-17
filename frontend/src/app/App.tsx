import { useDailyMlStatus } from '@/entities/daily-ml';
import { useCatchupState } from '@/entities/market-data';
import { usePortfolioQuery } from '@/entities/portfolio';
import { DailyMlPage } from '@/pages/daily-ml/DailyMlPage';
import { MarketDataPage } from '@/pages/market-data/MarketDataPage';
import { PortfolioPage } from '@/pages/portfolio/PortfolioPage';
import { PortfolioPlanPage } from '@/pages/portfolio-plan/PortfolioPlanPage';
import { ToastHost } from '@/shared/ui/toast/ToastHost';
import { AppHeader } from '@/widgets/app-header/AppHeader';
import { ProcessRail } from '@/widgets/process-rail/ProcessRail';

import { AppProviders } from './providers';
import { useRoute, type RouteName } from './router';
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
 * **Состояние процессов читает тоже оболочка**, а не их разделы. Иначе опрос
 * гаснет при переходе в портфель, и баннер живёт на устаревших числах — а
 * FR-006 требует, чтобы идущая работа была видна из любого раздела. Процессов
 * два — догон и ранжирование, — и они читаются двумя запросами: один не
 * заменяет другой.
 */
export function AppShell() {
  const route = useRoute();
  const portfolio = usePortfolioQuery();
  const catchup = useCatchupState();
  // Состояние ранжирования читает оболочка по той же причине, что и состояние
  // догона: идущий прогон виден из любого раздела, и опрос не должен гаснуть
  // при переходе в портфель.
  const dailyMl = useDailyMlStatus();

  return (
    <div className="app-shell">
      <AppHeader data={portfolio.data} />

      <ProcessRail state={catchup.data} dailyMl={dailyMl.data} route={route} />

      <Section route={route} />
    </div>
  );
}

/** Раздел по маршруту. Портфель — раздел по умолчанию. */
function Section({ route }: { route: RouteName }) {
  if (route === 'market-data') return <MarketDataPage />;
  if (route === 'daily-ml') return <DailyMlPage />;
  if (route === 'portfolio-plan') return <PortfolioPlanPage />;
  return <PortfolioPage />;
}
