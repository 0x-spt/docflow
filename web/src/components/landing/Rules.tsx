import { Section } from "@/components/ui";

const RULES = [
  { when: "УПД или накладная от поставщика", then: "1С: поступление товаров", alert: false },
  { when: "Контрагент из вашего списка", then: "CRM: сделка его менеджера", alert: false },
  { when: "Сумма выше порога", then: "на согласование руководителю", alert: false },
  { when: "Проверка не сошлась", then: "оператору в кабинет, не в учёт", alert: true },
];

/** [03] Распределение по правилам: «если → то». Настраивается на внедрении. */
export function Rules() {
  return (
    <Section num="03" id="rules" title="Каждый документ — по своему адресу" size="display">
      <div className="grid gap-10 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <p className="text-xl leading-snug">
            Тип документа, контрагент, сумма, результат проверки — по ним DocFlow решает, куда отправить данные и
            кому показать документ.
          </p>
          <p className="mt-6 text-base leading-relaxed text-muted">
            Правила настраиваем на внедрении под ваш процесс и меняем, когда процесс меняется.
          </p>
        </div>
        <table className="w-full border-2 border-ink bg-card shadow-hard lg:col-span-8">
          <caption className="sr-only">Примеры правил распределения</caption>
          <thead>
            <tr className="border-b-2 border-ink bg-ink text-left font-mono text-sm text-paper">
              <th scope="col" className="w-1/2 px-4 py-3 font-medium">
                если
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                то
              </th>
            </tr>
          </thead>
          <tbody>
            {RULES.map((r) => (
              <tr key={r.when} className="border-b-2 border-ink last:border-b-0">
                <td className="px-4 py-4 align-top text-base leading-snug sm:text-lg">{r.when}</td>
                <td
                  className={`border-l-2 border-ink px-4 py-4 align-top font-mono text-sm leading-snug sm:text-base ${r.alert ? "bg-paper font-medium text-accent-text" : ""}`}
                >
                  {r.then}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
