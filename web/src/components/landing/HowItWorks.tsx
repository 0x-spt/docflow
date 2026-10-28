import { Section } from "@/components/ui";

const STEPS = [
  {
    title: "Фото или скан",
    text: "Снимок с телефона, скан или PDF — загрузкой в кабинете или через API. Кривой кадр выравнивается.",
  },
  {
    title: "Чистый PDF с текстом",
    text: "Ровная страница с текстовым слоем: документ ищется и копируется, как набранный.",
  },
  {
    title: "Данные по схеме",
    text: "Поля и таблицы раскладываются по схеме вашего документа. Схема настраивается под нишу.",
  },
  {
    title: "Проверка и передача",
    text: "Правила и справочники сверяют каждое поле. Чистое уходит в вашу систему, спорное — оператору.",
  },
];

/** [02] Как работает: четыре шага — настоящая последовательность, поэтому с номерами. */
export function HowItWorks() {
  return (
    <Section num="02" id="how" title="Четыре шага от бумаги до вашей системы" size="display">
      <ol className="grid gap-0.5 border-2 border-ink bg-ink sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => (
          <li key={s.title} className="bg-card p-6 sm:p-7">
            <span className="block font-mono text-6xl font-medium leading-none tracking-tight text-ink">
              {String(i + 1).padStart(2, "0")}
            </span>
            <h3 className="mt-10 text-xl font-semibold tracking-tight">{s.title}</h3>
            <p className="mt-2 leading-relaxed text-muted">{s.text}</p>
          </li>
        ))}
      </ol>
    </Section>
  );
}
