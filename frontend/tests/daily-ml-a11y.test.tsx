/**
 * Доступность разделов «Ранжирование» и «План портфеля» (FR-072).
 *
 * Проверяется то, что проверяемо разметкой: состояние передано текстом, а не
 * только цветом; панель прогона закрывается по Escape и возвращает фокус;
 * индикатор активности процентом готовности не притворяется; широкие таблицы
 * лежат в собственном контейнере прокрутки.
 *
 * Высота элементов от 44 px, ширина от 360 px и `prefers-reduced-motion`
 * заданы стилями артефакта и здесь не дублируются: jsdom вёрстку не считает, и
 * «проверка», не считающая её, давала бы ложную уверенность. Эти правила
 * проверяются сверкой с артефактом (contracts/ui-states.md §6).
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { RouteName } from '@/app/router';

import { historyFixture, runFixture, statusFixture } from './msw/daily-ml';
import { http, HttpResponse, server } from './msw/server';

function renderAt(route: RouteName, path: string) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  });

  window.history.pushState(null, '', path);
  navigate(route);

  return render(
    <AppProviders client={client}>
      <AppShell />
    </AppProviders>,
  );
}

describe('доступность раздела «Ранжирование»', () => {
  it('идущий прогон не показывает ни доли готовности, ни индикатора процента', async () => {
    server.use(
      http.get('*/api/daily-ml/status', () =>
        HttpResponse.json(
          statusFixture({
            status: 'running',
            current: {
              asof_date: '2026-09-11',
              started_at: '2026-09-11T17:30:04+00:00',
              attempt: 1,
            },
          }),
        ),
      ),
    );

    renderAt('daily-ml', '/daily-ml');

    await screen.findByRole('heading', { name: 'Идёт ранжирование' });
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    // Точки анимации — украшение: читать их с экрана нечего.
    expect(document.querySelector('.process-glyph')).toHaveAttribute('aria-hidden', 'true');
  });

  it('состояние прогона в истории передано словом, а не только цветом', async () => {
    server.use(
      http.get('*/api/daily-ml/runs', () =>
        HttpResponse.json(historyFixture([runFixture({ status: 'failed' })])),
      ),
    );

    renderAt('daily-ml', '/daily-ml');

    const table = await screen.findByRole('table');
    expect(within(table).getByText('Отказ')).toBeInTheDocument();
  });

  it('панель прогона закрывается по Escape и возвращает фокус вызвавшей кнопке', async () => {
    renderAt('daily-ml', '/daily-ml');

    const open = await screen.findByRole('button', { name: /Открыть прогон за/ });
    await userEvent.click(open);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(open).toHaveFocus();
  });

  it('широкие таблицы лежат в своём контейнере прокрутки', async () => {
    renderAt('daily-ml', '/daily-ml');

    const table = await screen.findByRole('table');
    const frame = table.closest('.ml-table-frame');

    expect(frame).not.toBeNull();
    // Контейнер получает фокус с клавиатуры: иначе прокрутить таблицу без мыши
    // невозможно.
    expect(frame).toHaveAttribute('tabindex', '0');
    expect(frame).toHaveAttribute('aria-label', 'История прогонов');
  });

  it('кнопка паузы сообщает своё состояние, а не только подпись', async () => {
    server.use(
      http.get('*/api/daily-ml/status', () =>
        HttpResponse.json(statusFixture({ status: 'paused', paused: true })),
      ),
    );

    renderAt('daily-ml', '/daily-ml');

    const pause = await screen.findByRole('button', { name: /Возобновить автоматическое/ });
    expect(pause).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('доступность раздела «План портфеля»', () => {
  it('таблица плана объяснена текстом, на который она ссылается', async () => {
    renderAt('portfolio-plan', '/portfolio-plan');

    const table = await screen.findByRole('table');
    const describedBy = table.getAttribute('aria-describedby');

    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy as string)).toHaveTextContent(
      /Доля после плана — от всего счёта/,
    );
  });

  it('панель параметров закрывается по Escape и возвращает фокус', async () => {
    renderAt('portfolio-plan', '/portfolio-plan');

    const open = await screen.findByRole('button', { name: 'Параметры расчёта' });
    await userEvent.click(open);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(open).toHaveFocus();
  });

  it('ошибка ввода объявляется, а не только подсвечивается', async () => {
    renderAt('portfolio-plan', '/portfolio-plan');

    await userEvent.click(await screen.findByRole('button', { name: 'Параметры расчёта' }));
    await userEvent.type(screen.getByLabelText('Комиссия за одну сторону, %'), 'ноль');
    await userEvent.click(screen.getByRole('button', { name: 'Пересчитать план' }));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});

describe('навигация', () => {
  it('активный раздел отмечен не только оформлением', async () => {
    renderAt('daily-ml', '/daily-ml');

    const link = await screen.findByRole('link', { name: 'Ранжирование' });
    expect(link).toHaveAttribute('aria-current', 'page');
  });
});
