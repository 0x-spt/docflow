import { Section } from "@/components/ui";

type State = "ready" | "progress" | "soon";

const ITEMS: { name: string; text: string; state: State }[] = [
  { name: "API", text: "Загрузка документов, получение данных и PDF", state: "ready" },
  { name: "Вебхуки", text: "Готовый документ сам приходит в вашу систему, с подписью", state: "ready" },
  { name: "Excel", text: "Выгрузка данных таблицей", state: "progress" },
  { name: "Telegram", text: "Отправка фото документа боту", state: "progress" },
  { name: "1С", text: "Документы сразу в базу 1С", state: "soon" },
  { name: "Битрикс24", text: "Данные в карточки CRM", state: "soon" },
  { name: "amoCRM", text: "Данные в сделки", state: "soon" },
];

const LABEL: Record<State, string> = { ready: "работает", progress: "в работе", soon: "скоро" };
const SQUARE: Record<State, string> = { ready: "bg-ink", progress: "bg-muted", soon: "bg-card" };

/** [07] Интеграции: статус у каждой — без обещаний сверх сделанного. */
export function Integrations() {
  return (
    <Section num="07" id="integrations" title="Куда уходят данные" size="display">
      <ul className="border-t-2 border-ink">
        {ITEMS.map((it) => (
          <li
            key={it.name}
            className="grid grid-cols-[1fr_auto] items-baseline gap-x-6 gap-y-1 border-b-2 border-ink py-5 sm:grid-cols-[12rem_1fr_9rem]"
          >
            <span className="text-2xl font-semibold tracking-tight sm:text-3xl">{it.name}</span>
            <span className="col-span-2 row-start-2 text-lg text-muted sm:col-span-1 sm:row-start-auto">
              {it.text}
            </span>
            <span className="col-start-2 row-start-1 inline-flex items-center gap-2 font-mono text-sm font-medium sm:col-start-auto sm:row-start-auto sm:justify-self-end">
              <span aria-hidden="true" className={`inline-block size-3 border-2 border-ink ${SQUARE[it.state]}`} />
              {LABEL[it.state]}
            </span>
          </li>
        ))}
      </ul>
    </Section>
  );
}
