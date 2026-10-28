import { Section } from "@/components/ui";

const POINTS = [
  {
    title: "Ошибки",
    text: "Цифры переносят с бумаги руками. Опечатку в сумме или реквизитах находят поздно — когда она уже в учёте.",
  },
  {
    title: "Время",
    text: "Документ уже заполнен, но его набирают заново, поле за полем. Чем больше бумаги, тем больше часов.",
  },
  {
    title: "Не та работа",
    text: "Бухгалтер, логист или администратор занят вводом вместо своей работы.",
  },
];

/** [01] Проблема: ручной перенабор. Без цифр — их не измеряли. */
export function Problem() {
  return (
    <Section num="01" id="problem" title="Перенабор бумаги — работа, которую можно не делать" size="display">
      <ul className="grid gap-0.5 border-2 border-ink bg-ink md:grid-cols-3">
        {POINTS.map((p) => (
          <li key={p.title} className="bg-card p-6 sm:p-8">
            <h3 className="text-2xl font-semibold tracking-tight">{p.title}</h3>
            <p className="mt-3 text-lg leading-relaxed text-muted">{p.text}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
