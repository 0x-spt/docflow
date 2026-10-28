import { Section } from "@/components/ui";

const MODES = [
  {
    title: "Облако",
    text: "Быстрый старт без своего железа. Документы обрабатываются в облачном режиме — в нём сделан наш замер точности.",
  },
  {
    title: "Ваш сервер",
    text: "DocFlow и модель работают в вашем контуре. Документы и персональные данные не уходят за периметр.",
  },
];

/** [09] Данные и 152-ФЗ: облако или сервер клиента. Честно: локальная модель — черновик для оператора. */
export function Security() {
  return (
    <Section num="09" id="security" title="Облако или ваш сервер — решаете вы" meta="152-ФЗ" size="display">
      <div className="grid gap-10 lg:grid-cols-12">
        <ul className="grid gap-0.5 self-start border-2 border-ink bg-ink sm:grid-cols-2 lg:col-span-7">
          {MODES.map((m) => (
            <li key={m.title} className="bg-card p-6 sm:p-8">
              <h3 className="text-3xl font-semibold tracking-tight">{m.title}</h3>
              <p className="mt-3 text-lg leading-snug">{m.text}</p>
            </li>
          ))}
          <li className="bg-paper p-6 sm:col-span-2 sm:p-8">
            <p className="text-lg leading-snug">
              Режим под ваши требования к персональным данным и 152-ФЗ выбираем вместе на аудите.
            </p>
          </li>
        </ul>
        <ul className="grid gap-0.5 self-start border-2 border-ink bg-ink lg:col-span-5">
          <li className="bg-card p-6">
            <h3 className="font-mono text-sm text-accent-text">Честно о точности</h3>
            <p className="mt-2 text-lg leading-snug">
              Локальная модель даёт черновик. Его проверяет оператор, прежде чем данные уйдут в ваши системы.
            </p>
          </li>
          <li className="bg-card p-6">
            <h3 className="font-mono text-sm text-muted">Доступ</h3>
            <p className="mt-2 text-lg leading-snug">
              Кабинет — по логину и паролю, программы — по ключам API. Вебхуки подписаны, чтобы ваша система
              отличала их от подделки.
            </p>
          </li>
        </ul>
      </div>
    </Section>
  );
}
