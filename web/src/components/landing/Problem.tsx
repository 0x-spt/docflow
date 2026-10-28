import { Section } from "@/components/ui";

const POINTS = [
  {
    title: "Перенабор",
    text: "Документ уже заполнен, а его набирают заново: поле за полем, строка за строкой. Чем больше бумаги, тем больше часов.",
  },
  {
    title: "Опечатки в учёте",
    text: "Одна цифра в ИНН или в сумме — и ошибку находят на сверке, когда она уже в 1С и в отчётах.",
  },
  {
    title: "Документы в чатах",
    text: "Фото накладной в мессенджере, скан в почте, оригинал в папке. Что внесено, а что нет — не видно.",
  },
  {
    title: "Не та работа",
    text: "Бухгалтер, логист или администратор занят вводом данных вместо своей работы.",
  },
];

/** [01] Боль: ручной перенабор. Без цифр — их не измеряли. */
export function Problem() {
  return (
    <Section num="01" id="problem" title="Бумагу до сих пор перепечатывают руками" size="display">
      <ul className="grid gap-0.5 border-2 border-ink bg-ink sm:grid-cols-2 lg:grid-cols-4">
        {POINTS.map((p) => (
          <li key={p.title} className="bg-card p-6 sm:p-7">
            <h3 className="text-2xl font-semibold tracking-tight">{p.title}</h3>
            <p className="mt-3 text-lg leading-relaxed text-muted">{p.text}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
