/**
 * Доступность раздела «Рыночные данные» (FR-051–FR-057).
 *
 * Проверяется то, что проверяемо разметкой: смысл состояний передан текстом, а
 * не только цветом; индикатор хода объявлен и озвучен; панели закрываются по
 * Escape и возвращают фокус; индикатор активности скрыт от чтения с экрана и
 * процентом готовности не притворяется.
 *
 * Высота элементов от 44 px и перестроение таблицы на узком экране заданы
 * стилями артефакта и здесь не дублируются: jsdom не считает вёрстку, и
 * «проверка», не считающая её, давала бы ложную уверенность. Эти два правила
 * проверяются сверкой с артефактом (contracts/ui-states.md).
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';

import { anomalyCoverageFixture, catchupFixture } from './msw/market-data';
import { http, HttpResponse, server } from './msw/server';

function renderMarketData() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });

  window.history.pushState(null, '', '/market-data');
  navigate('market-data');

  return render(
    <AppProviders client={client}>
      <AppShell />
    </AppProviders>,
  );
}

describe('доступность раздела', () => {
  it('шкала сессий озвучивает все четыре числа', async () => {
    server.use(
      http.get('*/api/market-data/catchup', () => HttpResponse.json(catchupFixture('running'))),
    );

    renderMarketData();

    const track = await screen.findByRole('img', { name: /Собрано 18 из 90/ });
    expect(track).toHaveAccessibleName('Собрано 18 из 90; с ошибкой 1; пропущено 0; осталось 71');
  });

  it('значки состояния источников не читаются с экрана', async () => {
    server.use(
      http.get('*/api/market-data/catchup', () => HttpResponse.json(catchupFixture('running'))),
    );

    const { container } = renderMarketData();
    await screen.findByRole('heading', { name: /Собираем сессию/ });

    // Значки состояния источников показывают ход, а не смысл: состояние
    // названо словом рядом, поэтому от чтения с экрана значки скрыты.
    for (const glyph of container.querySelectorAll('.rail-item i')) {
      expect(glyph).toHaveAttribute('aria-hidden', 'true');
    }
  });

  it('расхождение «покрыто, но пусто» названо текстом, а не только цветом', async () => {
    server.use(
      http.get('*/api/market-data/coverage', () => HttpResponse.json(anomalyCoverageFixture())),
    );

    renderMarketData();

    await screen.findByRole('heading', { name: 'Группы данных' });
    const row = document.querySelector('[data-od-id="group-positions"]');
    expect(row).not.toBeNull();
    // Цвет значка — не единственный носитель смысла (FR-052).
    expect(within(row as HTMLElement).getByText('Значения отсутствуют')).toBeInTheDocument();
  });

  it('форма запуска закрывается по Escape и возвращает фокус', async () => {
    renderMarketData();

    const trigger = await screen.findByRole('button', { name: 'Ручной сбор' });
    await userEvent.click(trigger);
    expect(await screen.findByRole('dialog', { name: 'Запустить догон' })).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: 'Запустить догон' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('ошибка ввода объявляется и связана с полем', async () => {
    renderMarketData();

    await userEvent.click(await screen.findByRole('button', { name: 'Ручной сбор' }));
    const form = await screen.findByRole('dialog', { name: 'Запустить догон' });
    await userEvent.click(within(form).getByLabelText('Всё доступное окно'));

    const from = within(form).getByLabelText('Начало');
    await userEvent.clear(from);
    await userEvent.type(from, 'не дата');
    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    const alert = within(form).getByRole('alert');
    expect(alert).toHaveAttribute('id', 'launchError');
    expect(from).toHaveAttribute('aria-describedby', 'launchError');
    expect(from).toHaveAttribute('aria-invalid', 'true');
  });

  it('покрытие не выдаётся за качество: это две разные проверки', async () => {
    renderMarketData();

    // В строке группы — сколько собрано из возможного. Доля строк со
    // значениями к покрытию не сводится и живёт в сведениях (FR-009, FR-012).
    const section = (await screen.findByRole('heading', { name: 'Группы данных' })).closest(
      'section',
    ) as HTMLElement;
    expect(within(section).getByText('Собрано из возможного')).toBeInTheDocument();

    await userEvent.click(await screen.findByRole('button', { name: 'Сведения: Котировки' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Покрытие')).toBeInTheDocument();
    expect(within(dialog).getByText('Строк со значениями')).toBeInTheDocument();
  });
});
