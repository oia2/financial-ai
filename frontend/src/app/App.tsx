import { PortfolioPage } from '@/pages/portfolio/PortfolioPage';
import { ToastHost } from '@/shared/ui/toast/ToastHost';

import { AppProviders } from './providers';
import './styles/global.css';

export function App() {
  return (
    <AppProviders>
      <ToastHost>
        <PortfolioPage />
      </ToastHost>
    </AppProviders>
  );
}
