import { Button, Section } from "@/components/ui";

const SYSTEMS = [
  {
    name: "1С",
    scope: "Бухгалтерия, Управление торговлей, УНФ",
    text: "Документы поступления и реализации, контрагенты, номенклатура. Позиции сверяются со справочниками вашей базы.",
  },
  {
    name: "Битрикс24",
    scope: "CRM",
    text: "Сделки, контакты, компании и поля карточки. Документ попадает в нужную сделку.",
  },
  {
    name: "amoCRM",
    scope: "CRM",
    text: "Сделки, контакты и поля. Документ привязывается к клиенту и этапу воронки.",
  },
  {
    name: "Google Таблицы",
    scope: "таблицы",
    text: "Строка на каждый документ, позиции — отдельным листом. Таблица пополняется сама.",
  },
  {
    name: "Excel",
    scope: "таблицы",
    text: "Реестр документов за период: шапка и позиции, готов к сводным таблицам и сверке.",
  },
  {
    name: "Ваша система",
    scope: "API и вебхуки",
    text: "Готовый документ приходит сам: данные, исходный скан и цифровая копия. Вебхуки подписаны.",
  },
  {
    name: "Уведомления",
    scope: "почта и Telegram",
    text: "Оператору — документ, который нужно проверить. Ответственному — новый документ по его клиенту.",
  },
];

/** [02] Куда уходят данные — ядро продукта. Подключения — часть внедрения. */
export function Integrations() {
  return (
    <Section num="02" id="systems" title="Данные уходят туда, где вы с ними работаете" size="display">
      <ul className="border-t-2 border-ink">
        {SYSTEMS.map((s) => (
          <li
            key={s.name}
            className="grid gap-x-8 gap-y-2 border-b-2 border-ink py-6 md:grid-cols-[minmax(0,17rem)_1fr] lg:grid-cols-[20rem_1fr]"
          >
            <div>
              <h3 className="text-3xl font-semibold tracking-tight sm:text-4xl">{s.name}</h3>
              <p className="mt-1 font-mono text-sm text-muted">{s.scope}</p>
            </div>
            <p className="max-w-2xl self-center text-lg leading-snug">{s.text}</p>
          </li>
        ))}
      </ul>
      <div className="mt-10 flex flex-col gap-6 border-2 border-ink bg-card p-6 shadow-hard sm:p-8 md:flex-row md:items-center md:justify-between">
        <p className="max-w-2xl text-xl leading-snug">
          Подключение к вашим системам — часть внедрения: настраиваем на ваших базах и проверяем на пилоте, прежде
          чем запустить в работу.
        </p>
        <Button href="#audit" variant="primary" className="shrink-0 px-6">
          Обсудить мои системы
        </Button>
      </div>
    </Section>
  );
}
