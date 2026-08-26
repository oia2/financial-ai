import { PortfolioPage } from '@/pages/portfolio/PortfolioPage';

import { AppProviders } from './providers';
import './styles/global.css';

export function App() {
  return (
    <AppProviders>
      <PortfolioPage />
    </AppProviders>
  );
}
