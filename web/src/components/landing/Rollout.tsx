import { Section } from "@/components/ui";

const STEPS = [
  {
    title: "Аудит",
    text: "Смотрим ваши документы, объём и куда передавать данные. Описываем схему и правила проверки.",
  },
  {
    title: "Пилот",
    text: "Прогоняем ваши настоящие документы и замеряем точность. Цифры видите вы — до решения о подключении.",
  },
  { title: "Подключение", text: "API или вебхук в вашу систему, доступ в кабинет для операторов." },
];

/** [08] Как проходит внедрение: аудит → пилот → подключение. */
export function Rollout() {
  return (
    <Section num="08" id="rollout" title="Внедрение в три шага" size="display">
      <ol className="grid gap-10 md:grid-cols-3 md:gap-0">
        {STEPS.map((s, i) => (
          <li key={s.title} className="border-l-2 border-ink pl-6 md:pr-8">
            <span className="font-mono text-sm font-medium text-accent-text">
              шаг {i + 1} из {STEPS.length}
            </span>
            <h3 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{s.title}</h3>
            <p className="mt-3 text-lg leading-snug text-muted">{s.text}</p>
          </li>
        ))}
      </ol>
    </Section>
  );
}
