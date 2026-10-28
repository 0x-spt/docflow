import Link from "next/link";

import { Section } from "@/components/ui";

const NICHES = [
  { name: "Зуботехнические лаборатории", docs: "Заказ-наряды от руки, акты, накладные на материалы" },
  { name: "Бухгалтерия и аутсорс", docs: "Счета, акты, УПД, товарные накладные клиентов" },
  { name: "Логистика и склады", docs: "Транспортные накладные, ТТН, акты приёмки" },
  { name: "Автосервисы", docs: "Заказ-наряды, акты выполненных работ, накладные на запчасти" },
  { name: "Медицина", docs: "Направления, договоры на услуги, акты" },
  { name: "Кадры", docs: "Заявления, анкеты, копии документов сотрудников" },
];

/** [05] Ниши с примерами документов и плитка «ваша ниша». */
export function Niches() {
  return (
    <Section num="05" id="niches" title="Ниша не важна — важна схема документа" size="display">
      <ul className="grid gap-0.5 border-2 border-ink bg-ink sm:grid-cols-2 lg:grid-cols-3">
        {NICHES.map((n) => (
          <li key={n.name} className="flex min-h-48 flex-col justify-between gap-6 bg-card p-6">
            <h3 className="text-2xl font-semibold leading-tight tracking-tight">{n.name}</h3>
            <p className="font-mono text-sm leading-relaxed text-muted">{n.docs}</p>
          </li>
        ))}
        <li className="flex flex-col justify-between gap-6 bg-ink p-6 text-paper sm:col-span-2 lg:col-span-3 lg:flex-row lg:items-end">
          <div>
            <h3 className="text-3xl font-semibold tracking-tight sm:text-4xl">Ваша ниша</h3>
            <p className="mt-3 max-w-xl text-lg text-paper/80">
              Покажите документ — опишем поля, правила и справочники под него. Это часть аудита.
            </p>
          </div>
          <Link
            href="#audit"
            className="inline-flex min-h-11 items-center self-start border-2 border-paper px-5 py-2.5 font-semibold hover:bg-paper hover:text-ink lg:self-auto"
          >
            Обсудить схему
          </Link>
        </li>
      </ul>
    </Section>
  );
}
