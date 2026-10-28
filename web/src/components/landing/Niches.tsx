import Link from "next/link";

import { Section } from "@/components/ui";

const NICHES = [
  {
    name: "Зуботехнические лаборатории",
    from: "Заказ-наряды от клиник, заполненные от руки",
    to: "1С и таблица взаиморасчётов с клиниками",
  },
  {
    name: "Бухгалтерия и аутсорс",
    from: "Счета, акты, УПД и накладные клиентов",
    to: "Базы 1С клиентов, реестр в Excel",
  },
  {
    name: "Логистика и склады",
    from: "ТТН, транспортные накладные, акты приёмки",
    to: "1С: Управление торговлей, реестр в Google Таблицах",
  },
  {
    name: "Автосервисы",
    from: "Заказ-наряды, акты работ, накладные на запчасти",
    to: "УНФ и карточка клиента в CRM",
  },
  {
    name: "Медицинские лаборатории",
    from: "Направления, договоры на услуги, акты",
    to: "Ваша учётная система через API",
  },
];

/** [07] Ниши: для каждой — что приходит и куда уходит. Плюс плитка «ваша ниша». */
export function Niches() {
  return (
    <Section num="07" id="niches" title="Что приходит и куда уходит в вашей нише" size="display">
      <ul className="grid gap-0.5 border-2 border-ink bg-ink sm:grid-cols-2 lg:grid-cols-3">
        {NICHES.map((n) => (
          <li key={n.name} className="flex flex-col gap-6 bg-card p-6">
            <h3 className="text-2xl font-semibold leading-tight tracking-tight">{n.name}</h3>
            <dl className="mt-auto space-y-3 text-base leading-snug">
              <div>
                <dt className="font-mono text-sm text-muted">приходит</dt>
                <dd>{n.from}</dd>
              </div>
              <div>
                <dt className="font-mono text-sm text-accent-text">уходит</dt>
                <dd className="font-medium">{n.to}</dd>
              </div>
            </dl>
          </li>
        ))}
        <li className="flex flex-col justify-between gap-6 bg-ink p-6 text-paper">
          <div>
            <h3 className="text-2xl font-semibold leading-tight tracking-tight">Ваша ниша</h3>
            <p className="mt-3 text-base leading-snug text-paper/80">
              Новая ниша — это новая схема документа, а не новая разработка. Опишем поля, правила и маршруты на
              аудите.
            </p>
          </div>
          <Link
            href="#audit"
            className="inline-flex min-h-11 items-center self-start border-2 border-paper px-5 py-2.5 font-semibold hover:bg-paper hover:text-ink"
          >
            Обсудить мой документ
          </Link>
        </li>
      </ul>
    </Section>
  );
}
