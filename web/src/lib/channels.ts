// Разрешённые отправители почтового ящика: разбор поля формы. Формат тот же, что проверяет API.
const SENDER_RE = /^[^\s@]*@[^\s@.]+(?:\.[^\s@.]+)+$/;

/** Адреса (имя@домен) и домены (@домен) через запятую, точку с запятой, пробел или с новой строки. */
export function parseSenders(text: string): { senders: string[]; errors: string[] } {
  const senders: string[] = [];
  const errors: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const s = raw.trim().toLowerCase();
    if (!s) continue;
    if (!SENDER_RE.test(s)) errors.push(s);
    else if (!senders.includes(s)) senders.push(s);
  }
  return { senders, errors };
}

/** Пустой список значит «принимать письма от всех». */
export function isOpenToAll(senders: string[]): boolean {
  return senders.length === 0;
}
