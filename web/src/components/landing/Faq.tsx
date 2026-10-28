import { Section } from "@/components/ui";

const QA = [
  {
    q: "С какой 1С вы работаете?",
    a: "С типовыми конфигурациями: Бухгалтерия, Управление торговлей, УНФ — документы поступления и реализации, контрагенты, номенклатура. Доработанную базу смотрим на аудите.",
  },
  {
    q: "А если у нас CRM или просто таблицы?",
    a: "Битрикс24 и amoCRM — сделки, контакты и поля карточки. Google Таблицы и Excel — строка на документ, позиции отдельным листом. Можно несколько систем сразу: правила решают, что куда уходит.",
  },
  {
    q: "У нас своя система. Подключится?",
    a: "Да, через API и вебхуки: готовый документ сам приходит в вашу систему — данные, скан и цифровая копия в PDF.",
  },
  {
    q: "Читает ли рукописные документы?",
    a: "Да. В нашем прогоне в облачном режиме 10 рукописных заказ-нарядов прочитаны без ошибок в полях. На ваших документах точность замеряем в пилоте — до подключения.",
  },
  {
    q: "Что будет, если документ прочитан неверно?",
    a: "Правила ловят расхождения в реквизитах, суммах и датах. Такой документ не уходит в ваши системы, а попадает оператору: он правит поле и подтверждает.",
  },
  {
    q: "Сколько длится внедрение?",
    a: "Зависит от числа видов документов и систем. Срок называем по итогам аудита, вместе со схемой выгрузки.",
  },
  {
    q: "Где хранятся документы?",
    a: "В облаке или на вашем сервере — в локальном режиме документы не покидают ваш периметр. Режим и сроки хранения выбираем на аудите.",
  },
  {
    q: "Сколько это стоит?",
    a: "Зависит от объёма документов и систем, которые подключаем. Цену называем после аудита.",
  },
];

/** [11] Вопросы: <details> раскрываются без JS и доступны с клавиатуры. */
export function Faq() {
  return (
    <Section num="11" id="faq" title="Вопросы" size="display">
      <div className="border-t-2 border-ink">
        {QA.map((item) => (
          <details key={item.q} className="group border-b-2 border-ink">
            <summary className="flex min-h-11 cursor-pointer list-none items-start justify-between gap-6 py-5 text-xl font-semibold tracking-tight sm:text-2xl [&::-webkit-details-marker]:hidden">
              {item.q}
              <span aria-hidden="true" className="font-mono text-2xl leading-none text-accent-text group-open:hidden">
                +
              </span>
              <span
                aria-hidden="true"
                className="hidden font-mono text-2xl leading-none text-accent-text group-open:inline"
              >
                −
              </span>
            </summary>
            <p className="max-w-3xl pb-6 text-lg leading-relaxed text-muted">{item.a}</p>
          </details>
        ))}
      </div>
    </Section>
  );
}
