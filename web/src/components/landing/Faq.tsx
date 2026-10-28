import { Section } from "@/components/ui";

const QA = [
  {
    q: "Какие документы подходят?",
    a: "Печатные и рукописные: фото с телефона, сканы, PDF. Схема описывает поля конкретного документа — для новой ниши её настраиваем на аудите.",
  },
  {
    q: "Что будет, если модель прочитала неверно?",
    a: "Правила ловят расхождения в реквизитах, суммах и датах. Такой документ не уходит дальше, а попадает оператору: он правит поле и подтверждает.",
  },
  {
    q: "Где хранятся документы?",
    a: "В локальном режиме — только на вашем сервере. Подходящий режим и сроки хранения определяем на аудите.",
  },
  {
    q: "Сколько это стоит?",
    a: "Зависит от объёма и способа подключения. Цену называем после аудита.",
  },
  {
    q: "Как подключить к нашей системе?",
    a: "Через API и вебхуки — это работает уже сейчас. Готовые подключения к 1С, Битрикс24 и amoCRM — скоро.",
  },
  {
    q: "Можно зарегистрироваться самому?",
    a: "Нет. Доступ в кабинет выдаём после аудита, когда схема документа настроена.",
  },
];

/** [09] Вопросы: <details> раскрываются без JS и доступны с клавиатуры. */
export function Faq() {
  return (
    <Section num="09" id="faq" title="Вопросы" size="display">
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
