import { Button } from "@/components/ui";

const CHANNELS = ["Фото с телефона", "Скан", "PDF", "Почта", "Telegram"];

const SYSTEMS = [
  { name: "1С", what: "поступления, реализации" },
  { name: "Битрикс24", what: "сделки, контакты" },
  { name: "amoCRM", what: "сделки, контакты" },
  { name: "Google Таблицы", what: "строка на документ" },
  { name: "Excel", what: "реестр за период" },
  { name: "Ваша система", what: "API и вебхуки" },
];

const STAGES = ["читает, в том числе от руки", "проверяет каждое поле", "раскладывает по правилам"];

// Пучок линий со ступенькой: от N равных строк к одной точке по центру (или обратно).
// viewBox растягивается на высоту колонки, толщина линии не масштабируется.
function Fan({ count, direction }: { count: number; direction: "in" | "out" }) {
  const out = direction === "out";
  return (
    <svg aria-hidden="true" viewBox="0 0 100 600" preserveAspectRatio="none" className="absolute inset-0 block h-full w-full">
      {Array.from({ length: count }, (_, i) => {
        const y = ((i + 0.5) / count) * 600;
        const d = out ? `M0 300 H50 V${y} H100` : `M0 ${y} H50 V300 H100`;
        return (
          <path
            key={d}
            d={d}
            fill="none"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
            className={out ? "stroke-accent" : "stroke-ink"}
          />
        );
      })}
    </svg>
  );
}

function Core() {
  return (
    <div className="flex flex-col gap-5 border-2 border-ink bg-ink p-5 text-paper shadow-hard">
      <p className="text-3xl font-semibold tracking-tight">DocFlow</p>
      <ol className="space-y-2 font-mono text-sm leading-snug">
        {STAGES.map((s) => (
          <li key={s} className="flex gap-2">
            <span aria-hidden="true" className="mt-1.5 inline-block size-2 shrink-0 bg-accent" />
            {s}
          </li>
        ))}
      </ol>
      <p className="border-t-2 border-paper/30 pt-3 text-sm leading-snug text-paper/80">
        Сомнение — оператору, а не в учёт
      </p>
    </div>
  );
}

/** Схема «документ → DocFlow → системы». На широком экране — пучки линий, на узком — колонка сверху вниз. */
function FlowDiagram() {
  return (
    <figure className="mt-14 lg:mt-20">
      <figcaption className="sr-only">
        Документ приходит фото, сканом, PDF, почтой или в Telegram. DocFlow читает его, проверяет и раскладывает
        данные в 1С, Битрикс24, amoCRM, Google Таблицы, Excel или вашу систему через API.
      </figcaption>

      {/* широкий экран */}
      <div
        aria-hidden="true"
        className="hidden grid-cols-[10.5rem_minmax(2rem,1fr)_16rem_minmax(2rem,1fr)_19rem] grid-rows-[auto_27rem] lg:grid"
      >
        <p className="col-start-1 pb-3 font-mono text-sm text-muted">откуда</p>
        <p className="col-start-5 pb-3 font-mono text-sm text-muted">куда</p>
        <ul className="col-start-1 row-start-2 flex flex-col divide-y-2 divide-ink border-2 border-ink bg-card">
          {CHANNELS.map((c) => (
            <li key={c} className="flex flex-1 items-center px-3 py-2 font-mono text-sm">
              {c}
            </li>
          ))}
        </ul>
        <div className="relative col-start-2 row-start-2">
          <Fan count={CHANNELS.length} direction="in" />
        </div>
        <div className="col-start-3 row-start-2 flex flex-col justify-center py-6">
          <Core />
        </div>
        <div className="relative col-start-4 row-start-2">
          <Fan count={SYSTEMS.length} direction="out" />
        </div>
        <ul className="col-start-5 row-start-2 flex flex-col divide-y-2 divide-ink border-2 border-ink bg-card shadow-hard">
          {SYSTEMS.map((s) => (
            <li key={s.name} className="relative flex flex-1 items-center justify-between gap-3 px-4 py-3">
              <span className="absolute -left-[8px] top-1/2 size-3 -translate-y-1/2 bg-accent" />
              <span className="whitespace-nowrap text-xl font-semibold tracking-tight">{s.name}</span>
              <span className="text-right font-mono text-xs text-muted">{s.what}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* узкий экран */}
      <div aria-hidden="true" className="lg:hidden">
        <ul className="flex flex-wrap gap-2">
          {CHANNELS.map((c) => (
            <li key={c} className="border-2 border-ink bg-card px-2.5 py-1 font-mono text-sm">
              {c}
            </li>
          ))}
        </ul>
        <div className="ml-6 h-8 w-0.5 bg-ink" />
        <Core />
        <div className="ml-6 h-8 w-0.5 bg-accent" />
        <ul className="grid grid-cols-1 gap-0.5 border-2 border-ink bg-ink min-[400px]:grid-cols-2 sm:grid-cols-3">
          {SYSTEMS.map((s) => (
            <li key={s.name} className="flex flex-col gap-1 bg-card px-3 py-3">
              <span className="text-lg font-semibold leading-tight tracking-tight">{s.name}</span>
              <span className="font-mono text-xs text-muted">{s.what}</span>
            </li>
          ))}
        </ul>
      </div>
    </figure>
  );
}

/** Первый экран: обещание, подзаголовок, CTA и схема потока документа. */
export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="pb-16 pt-12 sm:pb-24 sm:pt-20">
      <h1
        id="hero-title"
        className="text-[2rem] font-semibold min-[360px]:text-[2.375rem] leading-[0.97] tracking-[-0.045em] text-balance sm:text-6xl md:text-7xl lg:text-[5.5rem]"
      >
        Сфотографировали документ — данные уже в&nbsp;вашей 1С, CRM и&nbsp;таблицах
      </h1>

      <div className="mt-10 grid gap-10 lg:mt-14 lg:grid-cols-12 lg:items-end lg:gap-8">
        <p className="max-w-2xl text-xl leading-relaxed sm:text-2xl sm:leading-snug lg:col-span-7">
          Счета, накладные, УПД, акты, заказ-наряды — DocFlow читает их, проверяет каждое поле и раскладывает
          данные по вашим системам. Без перенабора, без опечаток, без документов, потерянных в чатах.
        </p>
        <div className="flex flex-col gap-4 sm:flex-row lg:col-span-5 lg:justify-end">
          <Button href="#audit" variant="accent" className="whitespace-nowrap px-7 text-lg">
            Получить аудит
          </Button>
          <Button href="#systems" variant="ghost" className="whitespace-nowrap px-7 text-lg">
            Куда уходят данные
          </Button>
        </div>
      </div>

      <FlowDiagram />
    </section>
  );
}
