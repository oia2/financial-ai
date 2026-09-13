/**
 * Маршруты приложения: «Портфель» и «Рыночные данные».
 *
 * Своя реализация вместо библиотеки — сознательное решение (plan.md,
 * research.md R3). Разделов два, вложенности нет, параметров пути нет,
 * ленивой загрузки нет: `react-router` добавил бы зависимость и конфигурацию
 * под задачу, решаемую подпиской на `popstate`. Принцип II конституции
 * требует простейшего корректного решения и запрещает вводить библиотеки под
 * гипотетическое будущее. Появятся вложенные маршруты или загрузчики данных —
 * вернуться к библиотеке будет проще, чем сейчас доказать её необходимость.
 *
 * Прямая ссылка и перезагрузка работают без изменений инфраструктуры: nginx
 * уже отдаёт `index.html` на любой неизвестный путь (FR-002).
 *
 * **Переход между разделами.** Артефакт Open Design объявляет межстраничное
 * `@view-transition { navigation: auto; }`, потому что состоит из отдельных
 * HTML-файлов. В приложении тот же переход запускается вызовом
 * `document.startViewTransition()`; имена `view-transition-name` и
 * длительности сохранены. Это зафиксированное отступление от артефакта, и
 * оно единственное (Принцип VIII, research.md R4).
 */

import {
  useCallback,
  useSyncExternalStore,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from 'react';

export type RouteName = 'portfolio' | 'portfolio-plan' | 'daily-ml' | 'market-data';

const PATHS: Record<RouteName, string> = {
  portfolio: '/',
  'portfolio-plan': '/portfolio-plan',
  'daily-ml': '/daily-ml',
  'market-data': '/market-data',
};

export function pathOf(route: RouteName): string {
  return PATHS[route];
}

function routeOf(pathname: string): RouteName {
  const normalized = pathname.replace(/\/+$/, '');
  for (const [route, path] of Object.entries(PATHS) as [RouteName, string][]) {
    if (route !== 'portfolio' && normalized === path) return route;
  }
  // Портфель — раздел по умолчанию: неизвестный адрес открывает его, а не
  // пустой экран. Прямая ссылка на несуществующий раздел ведёт домой.
  return 'portfolio';
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener('popstate', listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('popstate', listener);
  };
}

function notify(): void {
  for (const listener of listeners) listener();
}

function snapshot(): RouteName {
  return routeOf(window.location.pathname);
}

/**
 * Переход в раздел.
 *
 * Повторный переход в текущий раздел записью в историю не считается: кнопка
 * «назад» не должна упираться в цепочку одинаковых адресов.
 */
export function navigate(route: RouteName): void {
  const target = PATHS[route];
  if (window.location.pathname === target) return;

  const go = () => {
    window.history.pushState(null, '', target);
    notify();
  };

  // Без поддержки API переход происходит мгновенно — артефакт этот случай
  // предусматривает («Без поддержки API работают обычные ссылки»).
  if (typeof document.startViewTransition === 'function') {
    document.startViewTransition(go);
  } else {
    go();
  }
}

export function useRoute(): RouteName {
  return useSyncExternalStore(subscribe, snapshot, () => 'portfolio' as RouteName);
}

/**
 * Ссылка на раздел.
 *
 * Остаётся настоящим `<a href>`: адрес виден в строке состояния, открывается
 * в новой вкладке и работает при отключённом JS-переходе. Перехватывается
 * только обычный левый клик — модификаторы и средняя кнопка отдаются
 * браузеру.
 */
export function RouteLink({
  route,
  children,
  ...rest
}: {
  route: RouteName;
  children: ReactNode;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'>) {
  const onClick = useCallback(
    (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      event.preventDefault();
      navigate(route);
    },
    [route],
  );

  return (
    <a href={PATHS[route]} onClick={onClick} {...rest}>
      {children}
    </a>
  );
}
