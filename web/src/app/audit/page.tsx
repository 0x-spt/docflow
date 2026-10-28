import type { Metadata } from "next";

import { AuditForm } from "@/components/AuditForm";
import { PublicPage } from "@/components/landing/PublicPage";

export const metadata: Metadata = {
  title: "Заявка на аудит — DocFlow",
  description: "Расскажите о ваших документах — разберём процесс и предложим пилот.",
};

export default function AuditPage() {
  return (
    <PublicPage>
      <div className="grid gap-10 py-12 sm:py-20 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <p className="font-mono text-base font-medium text-accent-text">[аудит]</p>
          <h1 className="mt-4 text-5xl font-semibold leading-[0.95] tracking-[-0.04em] sm:text-6xl">
            Заявка на аудит
          </h1>
          <p className="mt-6 text-xl leading-snug">
            Расскажите, какие у вас документы, сколько их и куда должны попадать данные.
          </p>
          <ol className="mt-8 space-y-3 border-l-2 border-ink pl-5 text-lg text-muted">
            <li>Созвонимся и посмотрим примеры документов.</li>
            <li>Покажем схему выгрузки в ваши системы и правила проверки.</li>
            <li>Предложим пилот на ваших документах с замером точности.</li>
          </ol>
          <p className="mt-8 text-base text-muted">Ответим в течение [СРОК ОТВЕТА].</p>
        </div>
        <div className="lg:col-span-8">
          <AuditForm />
        </div>
      </div>
    </PublicPage>
  );
}
