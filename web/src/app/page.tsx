import { Button, Field, Logo, Section, Stat, StatusBadge } from "@/components/ui";

// Заглушка: настоящий лендинг — в следующей задаче. Здесь — витрина примитивов.
export default function Home() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-6">
      <header className="mb-8 flex items-center justify-between">
        <Logo />
        <span className="font-mono text-sm text-muted">каркас сайта</span>
      </header>

      <Section num="01" title="Кнопки" meta="Button">
        <div className="flex flex-wrap gap-4">
          <Button variant="primary">Чёрная</Button>
          <Button variant="accent">Красная</Button>
          <Button variant="ghost">С рамкой</Button>
          <Button href="/login" variant="ghost">
            Ссылка
          </Button>
        </div>
      </Section>

      <Section num="02" title="Поле" meta="Field">
        <div className="grid max-w-md gap-4">
          <Field label="Email" name="email" type="email" placeholder="you@company.ru" />
          <Field label="Пароль" name="password" type="password" error="Минимум 10 символов" />
        </div>
      </Section>

      <Section num="03" title="Статусы и цифры" meta="StatusBadge / Stat">
        <div className="mb-6 flex flex-wrap gap-6">
          <StatusBadge status="ok" />
          <StatusBadge status="needs_review" />
          <StatusBadge status="failed" />
          <StatusBadge status="queued" />
          <StatusBadge status="processing" />
        </div>
        <div className="grid max-w-md grid-cols-2 gap-4">
          <Stat value="00" label="документов" />
          <Stat value="0%" label="без ручной проверки" />
        </div>
      </Section>
    </main>
  );
}
