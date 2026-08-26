import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import './toast.css';

interface ToastApi {
  show: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const VISIBLE_MS = 4000;

/** Кратковременное подтверждение действия — например, успешного обновления. */
export function ToastHost({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);

  const show = useCallback((text: string) => {
    setMessage(text);
    setTimeout(() => setMessage(null), VISIBLE_MS);
  }, []);

  const api = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast" role="status" aria-live="polite">
        {message}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  // Компоненты не обязаны знать о наличии хоста: без него уведомление
  // просто не показывается.
  return api ?? { show: () => undefined };
}
