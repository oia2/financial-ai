/**
 * Тестовая замена нативного `<dialog>`.
 *
 * jsdom 25 не реализует `showModal`, `show` и `close`, а панель запуска и
 * панель сведений о группе перенесены из артефакта именно как `dialog`: он
 * даёт закрытие по Escape и возврат фокуса без единой строки своего кода.
 *
 * Шим воспроизводит ровно то, что от элемента требуют требования фичи:
 * атрибут `open`, событие `close`, закрытие по Escape и возврат фокуса
 * вызвавшему элементу (FR-053). Настоящее поведение обеспечивает браузер —
 * здесь оно повторено, чтобы тесты проверяли **связку** «Escape → закрытие →
 * фокус вернулся», а не отсутствующую в jsdom реализацию.
 *
 * Файл живёт в тестах намеренно: в приложении полифила нет и быть не должно.
 */

const OPENERS = new WeakMap<HTMLDialogElement, Element | null>();

function focusInside(dialog: HTMLDialogElement): void {
  const focusable = dialog.querySelector<HTMLElement>(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  );
  focusable?.focus();
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return;

  const dialog = document.querySelector<HTMLDialogElement>('dialog[open]');
  if (dialog === null) return;

  event.preventDefault();
  dialog.close();
}

export function installDialogPolyfill(): void {
  const prototype = window.HTMLDialogElement?.prototype;
  if (prototype === undefined || typeof prototype.showModal === 'function') return;

  prototype.showModal = function showModal(this: HTMLDialogElement) {
    OPENERS.set(this, document.activeElement);
    this.setAttribute('open', '');
    focusInside(this);
  };

  prototype.show = function show(this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };

  prototype.close = function close(this: HTMLDialogElement, returnValue?: string) {
    if (!this.hasAttribute('open')) return;

    this.removeAttribute('open');
    if (returnValue !== undefined) this.returnValue = returnValue;

    const opener = OPENERS.get(this);
    if (opener instanceof HTMLElement) opener.focus();
    OPENERS.delete(this);

    this.dispatchEvent(new Event('close'));
  };

  document.addEventListener('keydown', onKeyDown);
}
