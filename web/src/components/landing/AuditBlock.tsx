import { AuditForm } from "@/components/AuditForm";
import { Section } from "@/components/ui";

/** [10] Заявка на аудит — та же форма, что на /audit. */
export function AuditBlock() {
  return (
    <Section num="10" id="audit" title="Начнём с аудита" size="display">
      <div className="grid gap-10 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <p className="text-xl leading-snug">
            Расскажите, какие у вас документы и куда должны попадать данные. Разберём процесс, предложим схему и
            пилот на ваших документах.
          </p>
          <p className="mt-6 text-base text-muted">Ответим в течение [СРОК ОТВЕТА].</p>
        </div>
        <div className="lg:col-span-8">
          <AuditForm />
        </div>
      </div>
    </Section>
  );
}
