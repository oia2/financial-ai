/**
 * Раздел «План портфеля» (US5, FR-056–FR-069).
 *
 * Проверяются утверждения, а не вёрстка: цена названа сессией, облигации видны
 * в составе как неизменяемые, выпавший актив назван с причиной, отказ — это
 * отсутствие плана, а не пустой состав, и действий исполнения в разделе нет.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';
import type { PlanDto } from '@/entities/portfolio-plan';

import { planFixture } from './msw/portfolio-plan';
import { http, HttpResponse, server } from './msw/server';

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  });

  window.history.pushState(null, '', '/portfolio-plan');
  navigate('portfolio-plan');

  return render(
    <AppProviders client={client}>
      <AppShell />
    </AppProviders>,
  );
}

function servePlan(plan: Partial<PlanDto> = {}, onRequest?: (body: unknown) => void) {
  server.use(
    http.post('*/api/portfolio-plan', async ({ request }) => {
      onRequest?.(await request.json());
      return HttpResponse.json(planFixture(plan));
    }),
  );
}

function refuse(code: string, status = 409, extra: Record<string, unknown> = {}) {
  server.use(
    http.post('*/api/portfolio-plan', () =>
      HttpResponse.json({ detail: { code, message: code, ...extra } }, { status }),
    ),
  );
}

describe('план портфеля', () => {
  it('показывает целевые позиции и разницу к текущим', async () => {
    servePlan();
    renderPage();

    const table = await screen.findByRole('table');
    const sber = within(table).getByText('SBER').closest('tr')!;

    const cells = within(sber).getAllByRole('cell');
    expect(cells[1]).toHaveTextContent('100');
    expect(cells[2]).toHaveTextContent('10');
    expect(within(sber).getByText('Продать 90 шт.')).toBeInTheDocument();
  });

  it('называет сессию, закрытием которой является цена', async () => {
    servePlan();
    renderPage();

    await screen.findByRole('table');
    // FR-065: цена — закрытие названной сессии, а не текущая котировка, и
    // принять одно за другое читатель не должен.
    expect(screen.getByText(/Цены: закрытие сессии/)).toHaveTextContent('11.09.2026');
  });

  it('показывает облигации в составе как неизменяемые', async () => {
    servePlan();
    renderPage();

    const table = await screen.findByRole('table');
    const bond = within(table).getByText('SU26238RMFS4').closest('tr')!;

    expect(within(bond).getByText(/вне отбора/)).toBeInTheDocument();
    expect(within(bond).getByText('Без изменения')).toBeInTheDocument();
  });

  it('называет выпавший актив с причиной', async () => {
    servePlan();
    renderPage();

    await screen.findByRole('table');
    expect(screen.getByText(/EQ_AST_XXXX/)).toHaveTextContent('не известен размер лота');
    // FR-067: вес такого актива остался деньгами и не перераспределён.
    expect(screen.getByText(/не перераспределён/)).toBeInTheDocument();
  });

  it('помечает план, построенный на вымышленных скорах', async () => {
    servePlan({ emulated: true });
    renderPage();

    expect(await screen.findByText('План построен на вымышленных скорах')).toBeInTheDocument();
  });

  it('отбор по изменению оставляет только нужные строки', async () => {
    servePlan();
    renderPage();

    await screen.findByRole('table');
    await userEvent.selectOptions(screen.getByLabelText(/Показать/), 'buy');

    const table = screen.getByRole('table');
    expect(within(table).getByText('LKOH')).toBeInTheDocument();
    expect(within(table).queryByText('SBER')).not.toBeInTheDocument();
  });
});

describe('параметры расчёта', () => {
  it('передаёт правило, лимит и комиссию на сервер', async () => {
    let sent: unknown = null;
    servePlan({}, (body) => {
      sent = body;
    });
    renderPage();

    await screen.findByRole('table');
    await userEvent.click(screen.getByRole('button', { name: 'Параметры расчёта' }));
    await userEvent.type(screen.getByLabelText('Лимит распределения, ₽'), '50000,00');
    await userEvent.click(screen.getByRole('button', { name: 'Пересчитать план' }));

    await screen.findByRole('table');
    // Запятая — десятичный разделитель ввода, сервер ждёт точку.
    expect(sent).toMatchObject({ policy: 'equal_top20', capital_limit: '50000.00' });
  });

  it('не отправляет неразбираемый ввод на сервер', async () => {
    let calls = 0;
    server.use(
      http.post('*/api/portfolio-plan', () => {
        calls += 1;
        return HttpResponse.json(planFixture());
      }),
    );
    renderPage();

    await screen.findByRole('table');
    expect(calls).toBe(1);

    await userEvent.click(screen.getByRole('button', { name: 'Параметры расчёта' }));
    await userEvent.type(screen.getByLabelText('Комиссия за одну сторону, %'), 'ноль');
    await userEvent.click(screen.getByRole('button', { name: 'Пересчитать план' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Комиссия — число в процентах');
    expect(calls).toBe(1);
  });

  it('«вся сумма» снимает лимит', async () => {
    let sent: Record<string, unknown> | null = null;
    servePlan({}, (body) => {
      sent = body as Record<string, unknown>;
    });
    renderPage();

    await screen.findByRole('table');
    await userEvent.click(screen.getByRole('button', { name: 'Параметры расчёта' }));
    await userEvent.type(screen.getByLabelText('Лимит распределения, ₽'), '50000');
    await userEvent.click(screen.getByRole('button', { name: 'Вся сумма' }));
    await userEvent.click(screen.getByRole('button', { name: 'Пересчитать план' }));

    await screen.findByRole('table');
    expect(sent).not.toHaveProperty('capital_limit');
  });
});

describe('план недоступен', () => {
  it.each([
    ['no_successful_ranking', /Успешного ранжирования ещё не было/],
    ['ranking_stale', /относится к другим данным/],
    ['broker_not_connected', /Счёт не подключён/],
    ['portfolio_stale', /Снимок счёта устарел/],
  ])('%s объясняется своей причиной', async (code, expected) => {
    refuse(code);
    renderPage();

    expect(await screen.findByText('Расчёт пока недоступен')).toBeInTheDocument();
    expect(screen.getByText(expected)).toBeInTheDocument();
    // Отказ — это отсутствие плана, а не пустой состав: пустая таблица
    // прочиталась бы как «продай всё».
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('нехватка активов называет требуемое и доступное число', async () => {
    refuse('insufficient_assets', 422, { required: 20, available: 2 });
    renderPage();

    expect(
      await screen.findByText(/Правило требует 20 активов, а в ранжировании их 2/),
    ).toBeInTheDocument();
  });
});

describe('границы раздела', () => {
  it('действий исполнения в разделе нет', async () => {
    servePlan();
    renderPage();

    await screen.findByRole('table');

    // FR-063: расчёт не меняет портфель и не предлагает совершить сделку.
    // Проверяется по всем управляющим элементам раздела, а не по одному имени.
    const main = screen.getByRole('main');
    const labels = within(main)
      .getAllByRole('button')
      .map((button) => button.textContent ?? '');

    for (const label of labels) {
      expect(label).not.toMatch(/купить|продать|заявк|исполн|отправить в терминал/i);
    }
    expect(
      within(main).getByText(/Расчёт портфель не меняет и заявок не отправляет/),
    ).toBeInTheDocument();
  });
});
