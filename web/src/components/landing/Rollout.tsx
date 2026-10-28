import { Button, Section } from "@/components/ui";

const STEPS = [
  {
    title: "Аудит",
    text: "Смотрим ваши документы, объём и системы. Без обязательств.",
    get: ["карта документооборота", "схема выгрузки в ваши системы", "оценка экономии времени"],
  },
  {
    title: "Пилот",
    text: "Прогоняем ваши настоящие документы и замеряем точность.",
    get: ["точность на ваших документах", "цифры — до решения о подключении"],
  },
  {
    title: "Подключение",
    text: "Подключаем к вашим системам и запускаем в работу.",
    get: ["выгрузка в 1С, CRM, таблицы или API", "правила распределения", "кабинет для операторов"],
  },
];

/** [10] Внедрение: аудит → пилот → подключение, и что клиент получает на каждом шаге. */
export function Rollout() {
  return (
    <Section num="10" id="rollout" title="Внедрение начинается с аудита" size="display">
      <ol className="grid gap-10 md:grid-cols-3 md:gap-0">
        {STEPS.map((s, i) => (
          <li key={s.title} className="flex flex-col border-l-2 border-ink pl-6 md:pr-8">
            <span className="font-mono text-sm font-medium text-accent-text">
              шаг {i + 1} из {STEPS.length}
            </span>
            <h3 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{s.title}</h3>
            <p className="mt-3 text-lg leading-snug text-muted">{s.text}</p>
            <h4 className="mt-6 font-mono text-sm">вы получаете</h4>
            <ul className="mt-2 space-y-1.5 text-lg leading-snug">
              {s.get.map((g) => (
                <li key={g} className="flex gap-2.5">
                  <span aria-hidden="true" className="mt-2.5 inline-block size-2 shrink-0 bg-ink" />
                  {g}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      <div className="mt-12">
        <Button href="#audit" variant="accent" className="px-7 text-lg">
          Записаться на аудит
        </Button>
      </div>
    </Section>
  );
}
