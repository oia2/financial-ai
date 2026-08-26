/**
 * Пользовательские предпочтения интерфейса.
 *
 * Здесь хранится только то, что относится к конкретному браузеру и не влияет
 * на данные: подсветка P&L и размер страницы таблицы. Настройки системы —
 * например, интервал автообновления — живут на сервере (FR-034).
 *
 * Доступ к хранилищу защищён: в приватном окне или при запрещённых куках
 * обращение к `localStorage` бросает исключение, и интерфейс не должен из-за
 * этого падать.
 */

export function readPreference(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePreference(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Предпочтение не сохранится — на работоспособность это не влияет.
  }
}
