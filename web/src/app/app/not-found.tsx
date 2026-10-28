import { Button } from "@/components/ui";

/** 404 внутри кабинета: документа нет или он другого клиента. */
export default function CabinetNotFound() {
  return (
    <div className="flex flex-col items-start gap-4 py-8">
      <h1 className="text-4xl font-semibold tracking-tight">Документ не найден</h1>
      <p className="max-w-xl text-muted">Ссылка устарела или документ загружен другим клиентом.</p>
      <Button href="/app" variant="ghost">
        К очереди
      </Button>
    </div>
  );
}
