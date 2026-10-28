import { Section } from "@/components/ui";

const STEPS = [
  {
    title: "Документ приходит",
    text: "Любым удобным способом: фото с телефона, скан, PDF, письмо на почту или сообщение в Telegram. Кривой кадр выравнивается.",
    tags: ["фото", "скан", "PDF", "почта", "Telegram"],
  },
  {
    title: "Читается и проверяется",
    text: "Печатный и рукописный текст раскладывается по полям и таблицам вашего документа. Каждое поле проходит проверку.",
    tags: ["поля", "таблицы", "реквизиты", "суммы"],
  },
  {
    title: "Уходит по правилам",
    text: "Проверенные данные — в ваши системы, спорный документ — оператору. К каждому документу — цифровая копия в PDF.",
    tags: ["1С", "CRM", "таблицы", "API"],
  },
];

/** [04] Как работает: три шага — настоящая последовательность, поэтому с номерами. */
export function HowItWorks() {
  return (
    <Section num="04" id="how" title="Три шага от бумаги до вашей системы" size="display">
      <ol className="grid gap-0.5 border-2 border-ink bg-ink md:grid-cols-3">
        {STEPS.map((s, i) => (
          <li key={s.title} className="flex flex-col bg-card p-6 sm:p-8">
            <span className="block font-mono text-6xl font-medium leading-none tracking-tight">
              {String(i + 1).padStart(2, "0")}
            </span>
            <h3 className="mt-10 text-2xl font-semibold tracking-tight">{s.title}</h3>
            <p className="mt-3 text-lg leading-relaxed text-muted">{s.text}</p>
            <ul className="mt-auto flex flex-wrap gap-1.5 pt-6" aria-label="Примеры">
              {s.tags.map((t) => (
                <li key={t} className="border-2 border-ink px-2 py-0.5 font-mono text-xs">
                  {t}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </Section>
  );
}
