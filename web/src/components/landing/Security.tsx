import { Section } from "@/components/ui";

/** [06] Безопасность и 152-ФЗ. Честно: локальная модель — черновик для оператора. */
export function Security() {
  return (
    <Section num="06" id="security" title="Данные остаются у вас" meta="152-ФЗ" size="display">
      <div className="grid gap-8 lg:grid-cols-12">
        <div className="lg:col-span-6">
          <p className="text-2xl leading-snug sm:text-3xl sm:leading-tight">
            Локальный режим: DocFlow и модель работают на вашем сервере. Документы и персональные данные не
            уходят за ваш периметр.
          </p>
        </div>
        <ul className="grid gap-0.5 self-start border-2 border-ink bg-ink lg:col-span-5 lg:col-start-8">
          <li className="bg-card p-6">
            <h3 className="font-mono text-sm uppercase text-accent-text">Честно о точности</h3>
            <p className="mt-2 text-lg leading-snug">
              Локальная модель даёт черновик. Его проверяет оператор, прежде чем данные уйдут дальше.
            </p>
          </li>
          <li className="bg-card p-6">
            <h3 className="font-mono text-sm uppercase text-muted">Доступ</h3>
            <p className="mt-2 text-lg leading-snug">
              Кабинет — по логину и паролю, программы — по ключам API. Вебхуки подписаны, чтобы ваша система
              отличала их от подделки.
            </p>
          </li>
          <li className="bg-card p-6">
            <h3 className="font-mono text-sm uppercase text-muted">Режим</h3>
            <p className="mt-2 text-lg leading-snug">
              Какой режим подойдёт под ваши требования к данным, решаем вместе на аудите.
            </p>
          </li>
        </ul>
      </div>
    </Section>
  );
}
