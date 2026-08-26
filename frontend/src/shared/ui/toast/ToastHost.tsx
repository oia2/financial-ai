import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

interface ToastApi {
  show: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Столько же, сколько в прототипе дизайна. */
const VISIBLE_MS = 2400;

/** Кратковременное подтверждение действия — например, успешного обновления. */
export function ToastHost({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((text: string) => {
    if (timer.current !== null) clearTimeout(timer.current);
    setMessage(text);
    timer.current = setTimeout(() => setMessage(null), VISIBLE_MS);
  }, []);

  const api = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className={`toast${message === null ? '' : ' visible'}`}
        role="status"
        aria-live="polite"
      >
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
