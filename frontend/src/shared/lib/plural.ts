/** Русское склонение существительных при числительных. */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;

  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** «1 позиция», «2 позиции», «5 позиций» — как в утверждённом дизайне. */
export function formatPositionCount(count: number): string {
  return `${count} ${plural(count, 'позиция', 'позиции', 'позиций')}`;
}
