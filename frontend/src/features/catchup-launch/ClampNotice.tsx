/**
 * Диапазон сужен сервером.
 *
 * Показываются оба: что человек ввёл и что принято к сбору. Введённый
 * диапазон — контекст показа, который держит интерфейс; серверным полем он не
 * является (FR-040).
 *
 * Сообщение не исчезает по таймеру: расхождение между запрошенным и принятым
 * человек должен увидеть тогда, когда посмотрит на экран, а не в первые
 * несколько секунд после нажатия.
 *
 * Принятый диапазон может отличаться от границ окна и по другой причине:
 * сервер сужает его до фактических пропусков.
 */

import { formatIsoDate } from '@/shared/lib/market-format';

export function ClampNotice({
  requestedFrom,
  requestedTill,
  acceptedFrom,
  acceptedTill,
}: {
  requestedFrom: string | null;
  requestedTill: string | null;
  acceptedFrom: string | null;
  acceptedTill: string | null;
}) {
  return (
    <section className="clamp-notice" role="status">
      <h3>Диапазон ограничен доступным окном</h3>
      <div className="clamp-ranges">
        <div>
          <small>Вы запросили</small>
          <span className="mono">
            {formatIsoDate(requestedFrom)} — {formatIsoDate(requestedTill)}
          </span>
        </div>
        <span aria-hidden="true">→</span>
        <div className="accepted">
          <small>Принят к сбору</small>
          <span className="mono">
            {formatIsoDate(acceptedFrom)} — {formatIsoDate(acceptedTill)}
          </span>
        </div>
      </div>
      <p>Сбор уже запущен в принятом диапазоне. Даты за пределами окна не будут обработаны.</p>
    </section>
  );
}
