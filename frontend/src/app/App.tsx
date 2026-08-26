import { PortfolioPage } from '@/pages/portfolio/PortfolioPage';
import { ToastHost } from '@/shared/ui/toast/ToastHost';

import { AppProviders } from './providers';
import './styles/design.css';
import './styles/app.css';

export function App() {
  return (
    <AppProviders>
      <ToastHost>
        <PortfolioPage />
      </ToastHost>
    </AppProviders>
  );
}
