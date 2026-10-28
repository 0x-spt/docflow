import Link from "next/link";
import { notFound } from "next/navigation";

import { DocumentData } from "@/components/app/DocumentData";
import { ExportsBlock } from "@/components/app/ExportsBlock";
import { PagePreview } from "@/components/app/PagePreview";
import { ReviewForm } from "@/components/app/ReviewForm";
import { StatusBadge } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fieldLabel, formatDateTime } from "@/lib/format";
import { isDocId } from "@/lib/ids";
import { INFO_CODES, notesByField } from "@/lib/review-form";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { ConnectorInfo, DocSchema, DocumentPayload, Issue } from "@/lib/types";

export const metadata = { title: "Документ — DocFlow" };

const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";
const PATH = /^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*))?$/;

/** «works[0].price» → «Строки таблицы работ, строка 1, цена за единицу»; поля нет в схеме — как есть. */
function placeOf(field: string | null, schema: DocSchema | null): string | null {
  if (!field) return null;
  const m = PATH.exec(field);
  const spec = m && schema?.fields.find((f) => f.name === m[1]);
  if (!m || !spec) return field === "_uncertain" ? null : field;
  if (m[2] === undefined) return fieldLabel(spec);
  const column = spec.columns?.find((c) => c.name === m[3]);
  return `${fieldLabel(spec)}, строка ${Number(m[2]) + 1}, ${(column ? fieldLabel(column) : m[3]).toLowerCase()}`;
}

function docTitle(doc: DocumentPayload): string {
  const number = doc.data?.number;
  if ((typeof number === "string" && number.trim()) || typeof number === "number") return `№${number}`;
  return doc.id.slice(0, 8);
}

async function loadSchema(doc: DocumentPayload): Promise<DocSchema | null> {
  try {
    return await apiFetchAuthed<DocSchema>(
      `/v1/schemas/${encodeURIComponent(doc.schema.slug)}?version=${doc.schema.version}`,
    );
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

function Issues({ issues, schema }: { issues: Issue[]; schema: DocSchema | null }) {
  // сначала то, что требует внимания, потом справочные (`corrected`)
  const sorted = [...issues].sort(
    (a, b) => Number(INFO_CODES.has(a.code ?? "")) - Number(INFO_CODES.has(b.code ?? "")),
  );
  return (
    <section aria-labelledby="issues-title" className="flex flex-col gap-3">
      <h2 id="issues-title" className="text-2xl font-semibold tracking-tight">
        Замечания <span className="font-mono text-lg font-medium text-muted">{issues.length}</span>
      </h2>
      {issues.length === 0 ? (
        <p className="text-muted">Проверки прошли без замечаний.</p>
      ) : (
        <ul className="border-2 border-ink bg-card">
          {sorted.map((issue, i) => {
            const info = INFO_CODES.has(issue.code ?? "");
            const place = placeOf(issue.field, schema);
            return (
              <li key={i} className="flex gap-3 border-b border-ink/30 px-4 py-2.5 last:border-b-0">
                <span
                  aria-hidden="true"
                  className={`mt-1.5 inline-block size-3 shrink-0 border-2 border-ink ${info ? "bg-card" : "bg-accent"}`}
                />
                <div className="min-w-0">
                  <p className="font-mono text-sm text-muted">
                    <span className="sr-only">{info ? "Для сведения" : "Проблема"}. </span>
                    {place ?? "Документ целиком"}
                  </p>
                  <p className={info ? "" : "font-medium text-accent-text"}>{issue.message ?? issue.code}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

const BTN =
  "inline-flex min-h-11 items-center justify-center border-2 border-ink bg-card px-5 py-2.5 font-semibold shadow-hard transition-transform active:translate-x-1.5 active:translate-y-1.5 active:shadow-none";

export default async function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isDocId(id)) notFound();
  let doc: DocumentPayload;
  try {
    doc = await apiFetchAuthed<DocumentPayload>(`/v1/documents/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();
    throw e;
  }
  const schema = await loadSchema(doc);
  const exports = doc.exports ?? [];
  const [me, catalog] = exports.length
    ? await Promise.all([requireMe(), apiFetchAuthed<ConnectorInfo[]>("/v1/connectors")])
    : [null, [] as ConnectorInfo[]];

  const inWork = doc.status === "queued" || doc.status === "processing";
  const reviewable = doc.status === "needs_review";
  // у failed PDF и превью бывают (упало извлечение, а не подготовка страниц), но `pages` тогда 0:
  // ссылку на PDF даём, а превью пробуем с первой страницы — нет файла → плашка
  const pdfHref = inWork ? null : `/app/files/${encodeURIComponent(doc.id)}/pdf`;
  const digitalHref = doc.digital_pdf_url ? `/app/files/${encodeURIComponent(doc.id)}/digital` : null;
  const pages = Math.max(doc.pages ?? 0, 1);
  const notes = notesByField(doc.issues);
  const data = doc.data ?? {};

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <Link href="/app" className={`inline-flex min-h-11 items-center self-start font-medium ${LINK}`}>
          К очереди
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-2">
            <h1 className="font-mono text-4xl font-medium tracking-tight sm:text-5xl">{docTitle(doc)}</h1>
            <p className="text-lg">{schema?.title ?? doc.schema.slug}</p>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
              <StatusBadge status={doc.status} />
              <span className="text-muted">
                Загружен <span className="font-mono tabular-nums">{formatDateTime(doc.created_at)}</span>
              </span>
              {doc.finished_at ? (
                <span className="text-muted">
                  Обработан <span className="font-mono tabular-nums">{formatDateTime(doc.finished_at)}</span>
                </span>
              ) : null}
            </div>
          </div>
          {pdfHref ? (
            <div className="flex flex-wrap gap-3">
              {/* обычные ссылки, не next/link: route handler не страница, предзагрузка скачала бы PDF зря */}
              <a href={pdfHref} download className={BTN}>
                Скачать PDF
              </a>
              {digitalHref ? (
                <a href={digitalHref} download className={BTN}>
                  Цифровая версия (PDF)
                </a>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {doc.review ? (
        <div className="border-2 border-ink bg-card p-4">
          <p className="font-medium">
            {doc.status === "failed" ? "Отклонён при проверке" : "Подтверждён при проверке"}{" "}
            <span className="font-mono text-sm font-normal tabular-nums text-muted">
              {formatDateTime(doc.review.at)}
            </span>
          </p>
          {doc.review.note ? <p className="mt-1 whitespace-pre-line">{doc.review.note}</p> : null}
        </div>
      ) : null}
      {/* у отклонённого причина уже показана выше, error лишь повторяет её */}
      {doc.status === "failed" && doc.error && !doc.review ? (
        <div className="border-2 border-accent-text bg-card p-4">
          <p className="font-medium text-accent-text">Ошибка обработки</p>
          <p className="mt-1 break-words">{doc.error}</p>
        </div>
      ) : null}

      {inWork ? (
        <div className="border-2 border-ink bg-card p-6">
          <p className="text-lg font-medium">Документ ещё обрабатывается</p>
          <p className="mt-1 text-muted">Обновите страницу через минуту — данные и превью появятся здесь.</p>
        </div>
      ) : (
        <div className="grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:items-start">
          <section aria-label="Страницы документа" className="lg:sticky lg:top-4">
            <div className="flex flex-col gap-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
              {Array.from({ length: pages }, (_, i) => (
                <PagePreview
                  key={i}
                  src={`/app/files/${encodeURIComponent(doc.id)}/pages/${i + 1}`}
                  page={i + 1}
                  pages={pages}
                  pdfHref={pdfHref}
                />
              ))}
            </div>
          </section>

          <div className="flex min-w-0 flex-col gap-8">
            {/* документ без данных (упал до извлечения) не проверялся — «без замечаний» было бы неправдой */}
            {doc.data || doc.issues.length ? <Issues issues={doc.issues} schema={schema} /> : null}
            <section aria-labelledby="data-title" className="flex flex-col gap-4">
              <h2 id="data-title" className="text-2xl font-semibold tracking-tight">
                {reviewable ? "Проверьте поля" : "Данные"}
              </h2>
              {!schema ? (
                <div className="border-2 border-accent-text bg-card p-4">
                  <p className="font-medium text-accent-text">
                    Схема «{doc.schema.slug}» версии {doc.schema.version} не найдена — форму не построить.
                  </p>
                  <pre className="mt-3 overflow-x-auto font-mono text-sm">{JSON.stringify(data, null, 2)}</pre>
                </div>
              ) : reviewable ? (
                <ReviewForm id={doc.id} schema={schema} data={data} notes={notes} />
              ) : doc.data ? (
                <DocumentData schema={schema} data={data} notes={notes} />
              ) : (
                <p className="text-muted">Данных нет: документ не дошёл до извлечения.</p>
              )}
            </section>
            <ExportsBlock docId={doc.id} exports={exports} catalog={catalog} canRetry={me?.user?.role === "owner"} />
          </div>
        </div>
      )}
    </div>
  );
}
