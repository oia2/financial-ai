/**
 * Форма запуска догона.
 *
 * Разметка перенесена из артефакта (`catchup-launch-drawer`): выбор групп,
 * флажок «Всё доступное окно», поля диапазона, предупреждение о длительности.
 *
 * Группы предлагаются **только те, что вернула сводка** (FR-021): произвольный
 * ввод группы невозможен, а список не дублируется в коде и потому не разойдётся
 * с сервером.
 *
 * Диапазон предзаполняется границами окна догона из ответа сводки. Выводить
 * их из строк сводки нельзя: окна групп различаются (research.md R2.2).
 */

import { useEffect, useRef, useState } from 'react';

import type { CoverageDto, GroupId, LaunchRequest } from '@/entities/market-data';
import { formatIsoDate } from '@/shared/lib/market-format';

/** `ДД.ММ.ГГГГ` → `ГГГГ-ММ-ДД`; `null`, если дата не разбирается. */
export function parseRuDate(value: string): string | null {
  const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(value.trim());
  if (match === null) return null;

  const [, day, month, year] = match;
  const iso = `${year}-${month}-${day}`;
  const parsed = new Date(`${iso}T00:00:00Z`);

  if (Number.isNaN(parsed.getTime())) return null;
  // 31.02.2026 разбирается движком в 03.03.2026 — такую дату принимать нельзя.
  if (parsed.toISOString().slice(0, 10) !== iso) return null;

  return iso;
}

export function LaunchDrawer({
  open,
  coverage,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  open: boolean;
  coverage: CoverageDto | undefined;
  pending: boolean;
  /** Сообщение отказа с сервера. Формулирует сервер, не форма (FR-038). */
  error: string | null;
  onSubmit: (request: LaunchRequest) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const fromRef = useRef<HTMLInputElement>(null);

  const window_ = coverage?.catchup_window;
  const [allGroups, setAllGroups] = useState(true);
  const [selected, setSelected] = useState<GroupId[]>([]);
  const [wholeWindow, setWholeWindow] = useState(true);
  const [from, setFrom] = useState('');
  const [till, setTill] = useState('');
  const [inputError, setInputError] = useState<string | null>(null);
  const [invalidField, setInvalidField] = useState<'from' | 'till' | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;

    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Диапазон предзаполняется один раз — когда окно догона впервые пришло с
  // сервера. Повторное заполнение «когда поле пусто» возвращало бы прежнее
  // значение при очистке поля: человек стирает дату, а она появляется снова.
  // Введённое значение сохраняется и после отказа: исправлять ошибку, а не
  // набирать заново (FR-041).
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || window_ === undefined) return;

    if (window_.date_from !== null) setFrom(formatIsoDate(window_.date_from));
    if (window_.date_till !== null) setTill(formatIsoDate(window_.date_till));
    prefilled.current = true;
  }, [window_]);

  const groups = coverage?.groups ?? [];

  function toggleGroup(group: GroupId, checked: boolean) {
    setAllGroups(false);
    setSelected((current) =>
      checked ? [...current, group] : current.filter((item) => item !== group),
    );
  }

  const chosen: GroupId[] = allGroups ? groups.map((row) => row.group) : selected;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setInputError(null);
    setInvalidField(null);

    if (chosen.length === 0) {
      setInputError('Выберите хотя бы одну группу данных.');
      return;
    }

    let dateFrom: string | null = null;
    let dateTill: string | null = null;

    if (!wholeWindow) {
      dateFrom = parseRuDate(from);
      if (dateFrom === null) {
        setInputError('Не удалось прочитать дату начала. Формат: ДД.ММ.ГГГГ.');
        setInvalidField('from');
        fromRef.current?.focus();
        return;
      }

      dateTill = parseRuDate(till);
      if (dateTill === null) {
        setInputError('Не удалось прочитать дату конца. Формат: ДД.ММ.ГГГГ.');
        setInvalidField('till');
        return;
      }

      if (dateFrom > dateTill) {
        setInputError('Начало диапазона должно быть не позже конца.');
        setInvalidField('from');
        fromRef.current?.focus();
        return;
      }
    }

    onSubmit({
      // Пусто означает «все»: умолчание принадлежит серверу, и повторять его
      // здесь незачем.
      groups: allGroups ? null : chosen,
      date_from: dateFrom,
      date_till: dateTill,
    });
  }

  return (
    <dialog className="drawer" ref={ref} onClose={onClose} aria-labelledby="launchTitle">
      <div className="drawer-heading">
        <h2 id="launchTitle">Запустить догон</h2>
        <button
          className="icon-button"
          type="button"
          aria-label="Закрыть настройки"
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <form onSubmit={submit} style={{ display: 'contents' }} noValidate>
        <div className="drawer-body">
          <p>Соберём только пропущенные сессии. Уже закрытые сессии повторно не запрашиваются.</p>

          <fieldset className="field-group">
            <legend>Группы данных</legend>
            <label className="check-row all-groups">
              <input
                type="checkbox"
                checked={allGroups}
                onChange={(event) => {
                  setAllGroups(event.target.checked);
                  if (event.target.checked) setSelected([]);
                }}
              />
              Все группы
            </label>
            <div>
              {groups.map((row) => (
                <label className="check-row" key={row.group}>
                  <input
                    type="checkbox"
                    name="group"
                    value={row.group}
                    checked={allGroups || selected.includes(row.group)}
                    onChange={(event) => toggleGroup(row.group, event.target.checked)}
                  />
                  {row.title.charAt(0).toUpperCase() + row.title.slice(1)}
                  <code>{row.group}</code>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="field-group">
            <legend>Диапазон</legend>
            <label className="check-row">
              <input
                type="checkbox"
                checked={wholeWindow}
                onChange={(event) => setWholeWindow(event.target.checked)}
              />
              Всё доступное окно
            </label>

            <div className="date-fields">
              <label htmlFor="dateFrom">
                Начало
                <input
                  id="dateFrom"
                  ref={fromRef}
                  type="text"
                  placeholder="ДД.ММ.ГГГГ"
                  value={from}
                  disabled={wholeWindow}
                  aria-invalid={invalidField === 'from'}
                  aria-describedby="launchError"
                  onChange={(event) => setFrom(event.target.value)}
                />
              </label>
              <label htmlFor="dateTill">
                Конец
                <input
                  id="dateTill"
                  type="text"
                  placeholder="ДД.ММ.ГГГГ"
                  value={till}
                  disabled={wholeWindow}
                  aria-invalid={invalidField === 'till'}
                  aria-describedby="launchError"
                  onChange={(event) => setTill(event.target.value)}
                />
              </label>
            </div>

            <p className="field-help">
              Если даты выходят за окно, в ответе будет показан фактически принятый диапазон.
            </p>
          </fieldset>

          <div className="run-notice">
            <div>
              <strong>Работа может занять десятки минут</strong>
              <p>
                Котировки: <span className="mono">1–2 с</span> на сессию. Позиции по фьючерсам:
                около <span className="mono">2,5 мин</span>. Страницу можно закрыть — сбор
                выполняется на сервере.
              </p>
            </div>
          </div>

          <p className="input-error" id="launchError" role="alert" hidden={!inputError && !error}>
            {inputError ?? error}
          </p>
        </div>

        <div className="drawer-footer">
          <button className="secondary-button" type="button" onClick={onClose}>
            Отмена
          </button>
          <button className="primary-button" type="submit" disabled={pending}>
            Запустить
          </button>
        </div>
      </form>
    </dialog>
  );
}
