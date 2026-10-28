import { AuditForm } from "@/components/AuditForm";
import { Section } from "@/components/ui";

const AFTER = [
  "Созвонимся и посмотрим примеры ваших документов.",
  "Покажем, какие данные и в какие системы будут уходить.",
  "Предложим пилот на ваших документах с замером точности.",
];

/** [12] Заявка на аудит — та же форма, что на /audit. */
export function AuditBlock() {
  return (
    <Section num="12" id="audit" title="Покажите ваши документы — покажем, куда они уйдут" size="display">
      <div className="grid gap-10 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <p className="text-xl leading-snug">
            Расскажите, какие у вас документы и куда должны попадать данные. Аудит без обязательств.
          </p>
          <ol className="mt-8 space-y-3 border-l-2 border-ink pl-5 text-lg text-muted">
            {AFTER.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ol>
          <p className="mt-8 text-base text-muted">Ответим в течение [СРОК ОТВЕТА].</p>
        </div>
        <div className="lg:col-span-8">
          <AuditForm />
        </div>
      </div>
    </Section>
  );
}
