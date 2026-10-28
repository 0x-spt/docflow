import { Button, StatusBadge } from "@/components/ui";

const ROWS = [
  { field: "Дата", value: "24.10.2026", ok: true, check: "дата корректна" },
  { field: "Сумма строк", value: "20 000,00", ok: true, check: "сложено по таблице" },
  { field: "Итог", value: "21 000,00", ok: false, check: "не сходится с суммой строк" },
];

/** Первый экран: заголовок во всю ширину, ниже — подзаголовок и пример результата проверки. */
export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="pb-16 pt-12 sm:pb-24 sm:pt-20">
      <h1
        id="hero-title"
        className="text-[2.75rem] font-semibold leading-[0.95] tracking-[-0.045em] text-balance sm:text-7xl lg:text-[6.75rem]"
      >
        Бумажные документы — сразу в вашу систему
      </h1>

      <div className="mt-12 grid gap-12 lg:mt-16 lg:grid-cols-12 lg:gap-8">
        <div className="lg:col-span-6">
          <p className="max-w-xl text-xl leading-relaxed sm:text-2xl sm:leading-snug">
            Фото или скан любого документа превращается в чистый PDF с поиском по тексту и в данные по вашей
            схеме. Каждое поле проверяется по правилам — спорное идёт человеку, остальное уходит в вашу систему.
          </p>
          <div className="mt-10 flex flex-col gap-4 sm:flex-row">
            <Button href="#audit" variant="accent" className="px-7 text-lg">
              Получить аудит
            </Button>
            <Button href="#how" variant="ghost" className="px-7 text-lg">
              Как это работает
            </Button>
          </div>
        </div>

        <figure className="lg:col-span-5 lg:col-start-8">
          <div className="border-2 border-ink bg-card shadow-hard">
            <div className="flex items-center justify-between border-b-2 border-ink px-4 py-3 font-mono text-sm">
              <span>заказ-наряд.jpg</span>
              <span className="text-muted">стр. 1 / 1</span>
            </div>
            <ul>
              {ROWS.map((r) => (
                <li
                  key={r.field}
                  className={`grid grid-cols-[1fr_auto] items-end gap-x-4 gap-y-1 border-b-2 border-ink px-4 py-4 ${r.ok ? "" : "bg-paper"}`}
                >
                  <span className="font-mono text-sm uppercase text-muted">{r.field}</span>
                  <span
                    className={`row-span-2 self-center font-mono text-2xl tabular-nums sm:text-3xl ${r.ok ? "" : "text-accent-text line-through decoration-2"}`}
                  >
                    {r.value}
                  </span>
                  <span className={`flex items-center gap-2 text-sm ${r.ok ? "" : "font-semibold text-accent-text"}`}>
                    <span
                      aria-hidden="true"
                      className={`inline-block size-2.5 shrink-0 ${r.ok ? "bg-ink" : "bg-accent"}`}
                    />
                    {r.ok ? "Верно: " : "Ошибка: "}
                    {r.check}
                  </span>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
              <StatusBadge status="needs_review" />
              <span className="font-mono text-sm">оператору в кабинет</span>
            </div>
          </div>
          <figcaption className="mt-5 max-w-md text-sm text-muted">
            Пример результата. Расхождение 21 000 против 20 000 — из настоящего прогона 28.10.2026: ошибка была в
            самом бумажном наряде.
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
