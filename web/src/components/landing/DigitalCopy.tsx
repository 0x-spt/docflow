import { Section } from "@/components/ui";

const LINES = [
  { name: "Коронка из циркония", qty: "2", sum: "9 000,00" },
  { name: "Вкладка культевая", qty: "1", sum: "2 500,00" },
  { name: "Модель гипсовая", qty: "2", sum: "1 000,00" },
];

const USES = ["в архив", "контрагенту", "на печать"];

/** Набросок фото: перекошенный лист с «рукописными» штрихами. */
function PhotoSheet() {
  return (
    <div className="relative mx-2 -rotate-2 border-2 border-ink bg-[#e4e1d8] p-5 shadow-hard">
      <div className="space-y-3" aria-hidden="true">
        <div className="h-3 w-2/3 bg-ink/50" />
        <div className="h-2 w-1/2 bg-ink/30" />
        <div className="mt-6 space-y-2.5 border-t-2 border-dashed border-ink/40 pt-4">
          {[88, 72, 80, 64, 76].map((w) => (
            <div key={w} className="h-2 bg-ink/35" style={{ width: `${w}%`, transform: `skewX(-${w % 9}deg)` }} />
          ))}
        </div>
        <div className="ml-auto mt-6 h-3 w-1/3 bg-ink/50" />
      </div>
      <p className="mt-5 font-mono text-sm">заказ-наряд.jpg</p>
    </div>
  );
}

/** Набросок цифровой версии: документ, набранный заново по распознанным данным. */
function DigitalSheet() {
  return (
    <div className="border-2 border-ink bg-card p-5 shadow-hard sm:p-6">
      <p className="text-lg font-semibold tracking-tight">Заказ-наряд № 17 от 14.10.2026</p>
      <table className="mt-4 w-full text-sm">
        <caption className="sr-only">Пример таблицы в цифровой версии</caption>
        <thead>
          <tr className="border-y-2 border-ink text-left font-mono text-xs">
            <th scope="col" className="py-1.5 pr-2 font-medium">
              Работа
            </th>
            <th scope="col" className="py-1.5 pr-2 text-right font-medium">
              Кол.
            </th>
            <th scope="col" className="py-1.5 text-right font-medium">
              Сумма
            </th>
          </tr>
        </thead>
        <tbody>
          {LINES.map((l) => (
            <tr key={l.name} className="border-b border-ink/30">
              <td className="py-1.5 pr-2 leading-snug">{l.name}</td>
              <td className="py-1.5 pr-2 text-right font-mono tabular-nums">{l.qty}</td>
              <td className="whitespace-nowrap py-1.5 text-right font-mono tabular-nums">{l.sum}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-ink font-semibold">
            <td colSpan={2} className="py-1.5">
              Итого
            </td>
            <td className="whitespace-nowrap py-1.5 text-right font-mono tabular-nums">12 500,00</td>
          </tr>
        </tfoot>
      </table>
      <p className="mt-5 border-t-2 border-ink pt-3 font-mono text-xs leading-relaxed text-muted">
        Цифровая версия, сформирована DocFlow по скану от 14.10.2026. Статус: проверен
      </p>
    </div>
  );
}

/** [06] Бонус: цифровая копия документа в PDF. */
export function DigitalCopy() {
  return (
    <Section num="06" id="digital-pdf" title="Плюс — цифровая копия каждого документа" meta="PDF" size="display">
      <div className="grid grid-cols-1 gap-12 lg:grid-cols-12 lg:items-center">
        <div className="lg:col-span-5">
          <p className="text-xl leading-snug sm:text-2xl">
            Не фото, а документ, набранный заново по распознанным данным: ровные поля, настоящие таблицы, итоги.
          </p>
          <p className="mt-5 text-lg leading-relaxed text-muted">
            Рядом хранится исходный скан с текстовым слоем — по нему работает поиск и копирование.
          </p>
          <ul className="mt-8 flex flex-wrap gap-2">
            {USES.map((u) => (
              <li key={u} className="border-2 border-ink bg-card px-3 py-1 font-mono text-sm">
                {u}
              </li>
            ))}
          </ul>
        </div>
        <figure className="grid grid-cols-1 items-center gap-8 sm:grid-cols-[minmax(0,0.8fr)_auto_minmax(0,1.2fr)] lg:col-span-7">
          <PhotoSheet />
          <span aria-hidden="true" className="justify-self-center font-mono text-3xl text-accent-text">
            <span className="sm:hidden">↓</span>
            <span className="hidden sm:inline">→</span>
          </span>
          <DigitalSheet />
          <figcaption className="text-sm text-muted sm:col-span-3">
            Пример: фото рукописного наряда и его цифровая версия. Данные в примере условные.
          </figcaption>
        </figure>
      </div>
    </Section>
  );
}
