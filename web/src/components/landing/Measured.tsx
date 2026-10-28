import { Section } from "@/components/ui";

/** [04] Измерено: только факты прогона 28.10.2026 на 10 рукописных заказ-нарядах. Ничего сверх них. */
export function Measured() {
  return (
    <Section num="04" id="measured" title="Измерено, а не обещано" meta="прогон 28.10.2026" size="display">
      <div className="border-2 border-ink bg-ink text-paper">
        <div className="grid gap-0.5 bg-paper/30 lg:grid-cols-12">
          <div className="flex flex-col justify-between gap-6 bg-ink p-6 sm:p-10 lg:col-span-7">
            <p className="font-mono text-[5.5rem] font-medium leading-[0.85] tracking-[-0.06em] tabular-nums sm:text-[10rem] lg:text-[12rem]">
              100%
            </p>
            <p className="max-w-sm text-xl leading-snug">полей прочитано верно в 10 рукописных заказ-нарядах</p>
          </div>
          <dl className="grid gap-0.5 lg:col-span-5">
            <div className="flex flex-col justify-between gap-4 bg-ink p-6 sm:p-8">
              <dt className="order-2 text-lg leading-snug">рукописных заказ-нарядов зуботехнической лаборатории</dt>
              <dd className="order-1 font-mono text-6xl font-medium leading-none tracking-tight tabular-nums">10</dd>
            </div>
            <div className="flex flex-col justify-between gap-4 bg-ink p-6 sm:p-8">
              <dt className="order-2 text-lg leading-snug">прошли без участия человека</dt>
              <dd className="order-1 font-mono text-6xl font-medium leading-none tracking-tight tabular-nums">
                7<span className="text-paper/60"> из 10</span>
              </dd>
            </div>
            <div className="flex flex-col justify-between gap-4 bg-ink p-6 sm:p-8">
              <dt className="order-2 text-lg leading-snug">
                отправлены на проверку — и во всех трёх расхождение было в самом документе
              </dt>
              <dd className="order-1 font-mono text-6xl font-medium leading-none tracking-tight tabular-nums">3</dd>
            </div>
          </dl>
        </div>
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-12">
        <div className="border-2 border-ink bg-card p-6 shadow-hard sm:p-8 lg:col-span-7">
          <p className="font-mono text-3xl font-medium tracking-tight tabular-nums sm:text-5xl">
            <span className="whitespace-nowrap">21 000</span> <span className="text-accent-text">≠</span>{" "}
            <span className="whitespace-nowrap">20 000</span>
          </p>
          <p className="mt-4 text-lg leading-snug">
            В одном наряде итог — 21 000 при сумме строк 20 000. На бумаге ошибку никто не заметил; проверка
            арифметики остановила документ.
          </p>
        </div>
        <p className="self-end text-base leading-relaxed text-muted lg:col-span-4 lg:col-start-9">
          Десять документов — маленькая выборка, а не гарантия. Точность на ваших документах замеряем в пилоте и
          показываем вам цифры до подключения.
        </p>
      </div>
    </Section>
  );
}
