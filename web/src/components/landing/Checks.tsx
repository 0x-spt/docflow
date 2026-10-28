import { Section } from "@/components/ui";

const CHECKS = [
  { what: "ИНН, КПП, ОГРН, СНИЛС", how: "Контрольные цифры: опечатка в одной цифре не пройдёт." },
  { what: "БИК и расчётный счёт", how: "Счёт сверяется с БИК банка по ключу." },
  { what: "Арифметика таблиц", how: "Количество × цена = сумма строки, сумма строк = итог, НДС сходится." },
  { what: "Даты", how: "Дата существует, не из будущего, идёт в правильном порядке." },
  { what: "Справочники", how: "Материалы, контрагенты, услуги — сверяются с вашими списками, опечатки исправляются." },
];

/** [03] Что проверяем. Перечислено только то, что реально есть в правилах проверки. */
export function Checks() {
  return (
    <Section num="03" id="checks" title="Каждое поле проходит проверку" size="display">
      <div className="grid gap-12 lg:grid-cols-12">
        <dl className="border-t-2 border-ink lg:col-span-7">
          {CHECKS.map((c) => (
            <div key={c.what} className="grid gap-1 border-b-2 border-ink py-5 sm:grid-cols-[14rem_1fr] sm:gap-6">
              <dt className="font-mono text-base font-medium">{c.what}</dt>
              <dd className="text-lg leading-snug text-muted">{c.how}</dd>
            </div>
          ))}
        </dl>
        <div className="lg:col-span-4 lg:col-start-9">
          <div className="border-2 border-ink bg-accent p-6 text-white shadow-hard sm:p-8">
            <p className="text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
              Сомнение — значит на проверку.
            </p>
            <p className="mt-4 text-lg leading-snug">
              Если правило не сошлось, документ не уходит дальше молча. Его открывает оператор, правит поле и
              подтверждает в один клик.
            </p>
          </div>
        </div>
      </div>
    </Section>
  );
}
