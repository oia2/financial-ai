import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { ServerUnreachableError } from '@/shared/api/client';

/**
 * Обрыв связи с сервером повторяется автоматически (FR-038), прикладные
 * ошибки — нет: повтор 422 не даст другого результата.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) =>
          error instanceof ServerUnreachableError && failureCount < 5,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 15_000),
        refetchOnWindowFocus: true,
        staleTime: 0,
      },
      mutations: { retry: false },
    },
  });
}

export function AppProviders({
  children,
  client,
}: {
  children: ReactNode;
  client?: QueryClient;
}) {
  const queryClient = client ?? createQueryClient();
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
