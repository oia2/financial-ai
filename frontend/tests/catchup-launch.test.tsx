/**
 * Форма запуска догона (US2, FR-019–FR-022, FR-041).
 *
 * Умолчания принадлежат серверу, группы предлагаются только известные, а
 * ошибка ввода привязана к полю и не стирает введённое.
 */

import { QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppShell } from '@/app/App';
import { AppProviders } from '@/app/providers';
import { navigate } from '@/app/router';

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

async function openForm() {
  renderMarketData();
  await userEvent.click(await screen.findByRole('button', { name: 'Ручной сбор' }));
  return screen.findByRole('dialog', { name: 'Запустить догон' });
}

function captureStart() {
  const captured: { body: unknown } = { body: undefined };
  server.use(
    http.post('*/api/market-data/catchup', async ({ request }) => {
      captured.body = await request.json();
      return HttpResponse.json({
        status: 'running',
        groups: ['quotes'],
        date_from: '2026-04-20',
        date_till: '2026-09-03',
        clamped: false,
        requested_sessions: 42,
      });
    }),
  );
  return captured;
}

describe('форма запуска догона', () => {
  it('по умолчанию — все группы и всё окно', async () => {
    const captured = captureStart();
    const form = await openForm();

    expect(within(form).getByLabelText('Все группы')).toBeChecked();
    expect(within(form).getByLabelText('Всё доступное окно')).toBeChecked();

    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    // Пустое тело: умолчания принадлежат серверу и здесь не повторяются.
    expect(captured.body).toEqual({});
  });

  it('предлагает только известные группы с их идентификаторами', async () => {
    const form = await openForm();

    for (const [title, id] of [
      ['Котировки', 'quotes'],
      ['Агрегаты', 'aggregates'],
      ['Глобальные ряды', 'global'],
      ['Позиции по фьючерсам', 'positions'],
      ['Справочники', 'reference'],
    ] as const) {
      const row = within(form).getByText(id).closest('label');
      expect(row).not.toBeNull();
      expect(row).toHaveTextContent(title);
    }
  });

  it('предупреждает о длительности и о том, что страницу можно закрыть', async () => {
    const form = await openForm();

    expect(within(form).getByText('Работа может занять десятки минут')).toBeInTheDocument();
    expect(within(form).getByText(/сбор выполняется на сервере/i)).toBeInTheDocument();
  });

  it('передаёт выбранные группы', async () => {
    const captured = captureStart();
    const form = await openForm();

    await userEvent.click(within(form).getByLabelText('Все группы'));
    await userEvent.click(within(form).getByRole('checkbox', { name: /Котировки/ }));
    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    expect(captured.body).toEqual({ groups: ['quotes'] });
  });

  it('предзаполняет диапазон границами окна догона', async () => {
    const form = await openForm();

    await userEvent.click(within(form).getByLabelText('Всё доступное окно'));

    // Окно приходит с сервера: выводить его из строк сводки нельзя, у групп
    // окна разные.
    expect(within(form).getByLabelText('Начало')).toHaveValue('20.04.2026');
    expect(within(form).getByLabelText('Конец')).toHaveValue('03.09.2026');
  });

  it('не отправляет форму без выбранных групп', async () => {
    const form = await openForm();

    await userEvent.click(within(form).getByLabelText('Все группы'));
    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    expect(within(form).getByRole('alert')).toHaveTextContent(
      'Выберите хотя бы одну группу данных.',
    );
  });

  it('неразбираемая дата: ошибка у поля, введённое сохранено', async () => {
    const form = await openForm();

    await userEvent.click(within(form).getByLabelText('Всё доступное окно'));
    const from = within(form).getByLabelText('Начало');
    await userEvent.clear(from);
    await userEvent.type(from, '31.02.2026');
    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    expect(within(form).getByRole('alert')).toHaveTextContent(/Не удалось прочитать дату начала/);
    expect(from).toHaveAttribute('aria-invalid', 'true');
    // Введённое значение сохранено: исправлять, а не набирать заново (FR-041).
    expect(from).toHaveValue('31.02.2026');
    expect(from).toHaveFocus();
  });

  it('начало позже конца: форма не отправляется, фокус к началу диапазона', async () => {
    const captured = captureStart();
    const form = await openForm();

    await userEvent.click(within(form).getByLabelText('Всё доступное окно'));
    const from = within(form).getByLabelText('Начало');
    await userEvent.clear(from);
    await userEvent.type(from, '03.09.2026');
    const till = within(form).getByLabelText('Конец');
    await userEvent.clear(till);
    await userEvent.type(till, '20.04.2026');

    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    expect(within(form).getByRole('alert')).toHaveTextContent(
      'Начало диапазона должно быть не позже конца.',
    );
    expect(from).toHaveFocus();
    expect(captured.body).toBeUndefined();
  });

  it('после исправления запуск проходит', async () => {
    const captured = captureStart();
    const form = await openForm();

    await userEvent.click(within(form).getByLabelText('Всё доступное окно'));
    const from = within(form).getByLabelText('Начало');
    await userEvent.clear(from);
    await userEvent.type(from, 'не дата');
    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));
    expect(within(form).getByRole('alert')).toHaveTextContent(/Не удалось прочитать дату начала/);

    await userEvent.clear(from);
    await userEvent.type(from, '01.06.2026');
    await userEvent.click(within(form).getByRole('button', { name: 'Запустить' }));

    expect(captured.body).toEqual({ date_from: '2026-06-01', date_till: '2026-09-03' });
  });
});
