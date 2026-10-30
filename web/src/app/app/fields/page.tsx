import Link from "next/link";

import { FieldLabelsForm } from "@/components/app/FieldLabelsForm";
import { ResetSchemaButton } from "@/components/app/ResetSchemaButton";
import { Button } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { FieldLabels, SchemaListItem } from "@/lib/types";

export const metadata = { title: "Поля — DocFlow" };

const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";

export default async function FieldsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const me = await requireMe();
  const isOwner = me.user?.role === "owner";
  const schemas = await apiFetchAuthed<SchemaListItem[]>("/v1/schemas");
  const wanted = typeof sp.schema === "string" ? sp.schema : undefined;
  const current = schemas.find((s) => s.slug === wanted) ?? schemas[0];
  const labels = current
    ? await apiFetchAuthed<FieldLabels>(
        `/v1/schemas/${encodeURIComponent(current.slug)}/labels`,
      )
    : null;

  // откат есть только у встроенного типа, перекрытого компанией: смотрим, есть ли встроенное тело
  let canReset = false;
  if (isOwner && current && !current.builtin) {
    try {
      await apiFetchAuthed(`/v1/schemas/${encodeURIComponent(current.slug)}?raw=true&builtin=true`);
      canReset = true;
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e;
    }
  }
  const saved = typeof sp.saved === "string" && /^\d+$/.test(sp.saved) ? sp.saved : null;

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">
          Поля документов
        </h1>
        <p className="mt-1 max-w-3xl text-muted">
          Названия появятся в очереди, карточке документа, цифровом PDF,
          таблицах, Excel и уведомлениях. Модель их не видит: распознавание идёт
          по исходным полям. Пустое поле — название по умолчанию. Ничего не
          отмечено в «В очереди» — показываем контрагента, сумму и дату.
        </p>
        {isOwner ? (
          <div className="mt-4">
            <Button variant="ghost" href="/app/fields/new">
              Новый тип документа
            </Button>
          </div>
        ) : (
          <p className="mt-2 font-medium">Названия меняет владелец аккаунта.</p>
        )}
      </div>

      {schemas.length === 0 ? (
        <div className="border-2 border-ink bg-card p-6">
          <p className="text-lg font-medium">Типов документов пока нет.</p>
        </div>
      ) : (
        <nav aria-label="Тип документа">
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {schemas.map((s) => {
              const active = s.slug === current?.slug;
              return (
                <li key={s.slug}>
                  <Link
                    href={`/app/fields?schema=${encodeURIComponent(s.slug)}`}
                    aria-current={active ? "page" : undefined}
                    className={`inline-flex min-h-11 items-center text-base ${active ? "font-semibold" : "font-medium"} ${LINK}`}
                  >
                    {s.title}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      )}

      {labels ? (
        <section aria-labelledby="labels-title" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <h2
              id="labels-title"
              className="text-2xl font-semibold tracking-tight"
            >
              {labels.title}
            </h2>
            {isOwner && current ? (
              <>
                <Button href={`/app/fields/${encodeURIComponent(current.slug)}/edit`}>
                  Изменить поля
                </Button>
                {canReset ? (
                  <ResetSchemaButton slug={current.slug} version={current.version} />
                ) : null}
              </>
            ) : null}
          </div>
          {saved ? (
            <p
              role="status"
              className="border-2 border-ink bg-card p-4 font-medium"
            >
              Сохранено: версия {saved}. Новые документы пойдут по ней,
              обработанные остаются на своей версии.
            </p>
          ) : null}
          <FieldLabelsForm
            key={labels.slug}
            slug={labels.slug}
            rows={labels.rows}
            readOnly={!isOwner}
            queueDefault={labels.queue_default}
          />
        </section>
      ) : null}
    </div>
  );
}
