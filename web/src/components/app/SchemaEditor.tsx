"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";

import { saveSchema } from "@/app/app/fields/actions";
import { SchemaFieldCard } from "@/components/app/SchemaFieldCard";
import { Button, Field } from "@/components/ui";
import {
  deleteBlockers,
  fromSchema,
  move,
  newColumn,
  newDoc,
  newField,
  removeColumn,
  removeField,
  titleResolver,
  toSchema,
  validateDoc,
  withNames,
  type EditorDoc,
  type EditorField,
} from "@/lib/field-builder/model";
import { rulePhrase } from "@/lib/field-builder/rules";
import { changeSummary } from "@/lib/field-builder/summary";
import { slugFromTitle } from "@/lib/field-builder/translit";
import type { DocSchema } from "@/lib/types";

const NO_CHANGES = "изменений нет";
const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";

/**
 * Редактор типа документа: шапка, поля, колонки таблицы, проверки. Сохранение — новая версия схемы.
 * В состоянии всегда документ после `withNames`: иначе ссылки правил на переименованные новые поля устаревают.
 */
export function SchemaEditor({
  initial,
  labels,
  dictionaries,
  takenSlugs,
}: {
  /** null — новый тип. */
  initial: DocSchema | null;
  labels: Record<string, string>;
  dictionaries: { name: string; title: string }[];
  takenSlugs: string[];
}) {
  const [doc, setDoc] = useState<EditorDoc>(() => withNames(initial ? fromSchema(initial) : newDoc()));
  const [slugTouched, setSlugTouched] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [shownAt, setShownAt] = useState(0); // счётчик показов ошибок: каждый раз переводим фокус наверх
  const [focusId, setFocusId] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const noteRef = useRef<HTMLDivElement>(null);

  const schema = useMemo(() => toSchema(doc), [doc]);
  const summary = changeSummary(initial, schema);
  // новый тип: правка — всё, что отличается от заготовки (иначе предупреждение было бы сразу при открытии)
  const pristine = useMemo(() => (initial ? null : toSchema(withNames(newDoc()))), [initial]);
  const dirty = pristine
    ? changeSummary(pristine, { ...schema, slug: pristine.slug }) !== NO_CHANGES
    : summary !== NO_CHANGES;
  const titleOf = useMemo(() => titleResolver(doc, labels), [doc, labels]);

  useEffect(() => {
    if (shownAt) noteRef.current?.focus();
  }, [shownAt]);

  useEffect(() => {
    if (!focusId) return;
    document.getElementById(focusId)?.focus();
    setFocusId(null);
  }, [focusId]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  /** Правка документа. `shifts` — индексы полей сдвигаются: старые ошибки по индексам больше не к месту. */
  function update(fn: (d: EditorDoc) => EditorDoc, shifts = false) {
    setDoc((d) => withNames(fn(d)));
    if (shifts) {
      setErrors({});
      setError(null);
    }
  }

  const setField = (i: number, f: EditorField, shifts = false) =>
    update((d) => ({ ...d, fields: d.fields.map((x, k) => (k === i ? f : x)) }), shifts);

  function addField() {
    const [next, f] = newField(doc);
    update(() => ({ ...next, fields: [...next.fields, f] }));
    setFocusId(`f-${f.uid}-title`);
  }

  function addColumn(i: number) {
    const [next, c] = newColumn(doc);
    update(() => ({
      ...next,
      fields: next.fields.map((x, k) => (k === i ? { ...x, columns: [...x.columns, c] } : x)),
    }));
    setFocusId(`f-${c.uid}-title`);
  }

  function onTitle(title: string) {
    update((d) => ({
      ...d,
      title,
      slug: d.isNew && !slugTouched ? (title.trim() ? slugFromTitle(title) : "") : d.slug,
    }));
  }

  function save() {
    if (pending) return;
    const found = validateDoc(doc, takenSlugs);
    if (Object.keys(found).length) {
      setErrors(found);
      setError(null);
      setShownAt((n) => n + 1);
      return;
    }
    setErrors({});
    setError(null);
    const sent = schema;
    start(async () => {
      // успех — redirect на «Поля»; сюда возвращаются только ошибки
      const res = await saveSchema(sent, doc.version);
      setError(res.error ?? null);
      setErrors(res.errors ?? {});
      setShownAt((n) => n + 1);
    });
  }

  const companyLabel = (key: string): string | undefined =>
    Object.hasOwn(labels, key) ? labels[key] || undefined : undefined;
  const ruleErrors = Object.entries(errors)
    .filter(([k]) => k.startsWith("rules."))
    .map(([k, msg]) => {
      const rule = doc.rules[Number(k.slice("rules.".length))];
      return rule ? `Правило «${rulePhrase(rule, titleOf)}»: ${msg}` : msg;
    });
  const hasErrors = Boolean(error) || Object.keys(errors).length > 0;
  const cancelHref = initial ? `/app/fields?schema=${encodeURIComponent(initial.slug)}` : "/app/fields";

  return (
    <div className="flex flex-col gap-8">
      {hasErrors ? (
        <div
          ref={noteRef}
          tabIndex={-1}
          role="alert"
          className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text"
        >
          <p>{error ?? errors.fields ?? "Исправьте отмеченное ниже."}</p>
          {/* правила документа пока без своего списка: их ошибки — здесь */}
          {ruleErrors.map((t, i) => (
            <p key={i} className="mt-1 text-sm">
              {t}
            </p>
          ))}
        </div>
      ) : null}

      <fieldset disabled={pending} className="m-0 flex min-w-0 flex-col gap-8 border-0 p-0">
        <section aria-labelledby="schema-head" className="flex flex-col gap-4 border-2 border-ink bg-card p-4">
          <h2 id="schema-head" className="text-2xl font-semibold tracking-tight">
            Тип документа
          </h2>
          <Field
            label="Название"
            name="schema-title"
            value={doc.title}
            maxLength={200}
            autoComplete="off"
            onChange={(e) => onTitle(e.target.value)}
            error={errors.title}
          />
          <Field
            as="textarea"
            rows={3}
            label="Описание — что это за документ (его видит модель)"
            name="schema-description"
            value={doc.description}
            onChange={(e) => update((d) => ({ ...d, description: e.target.value }))}
            error={errors.description}
          />
          {doc.isNew ? (
            <Field
              label="Служебное имя"
              name="schema-slug"
              value={doc.slug}
              maxLength={40}
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              hint="Латинские строчные буквы, цифры и «_». После сохранения не меняется."
              onChange={(e) => {
                setSlugTouched(true);
                update((d) => ({ ...d, slug: e.target.value }));
              }}
              error={errors.slug}
            />
          ) : null}
        </section>

        <section aria-labelledby="schema-fields" className="flex flex-col gap-4">
          <h2 id="schema-fields" className="text-2xl font-semibold tracking-tight">
            Поля
          </h2>
          {doc.fields.length ? (
            <ol className="flex flex-col gap-4">
              {doc.fields.map((f, i) => (
                <SchemaFieldCard
                  key={f.uid}
                  field={f}
                  index={i}
                  count={doc.fields.length}
                  errors={errors}
                  companyLabel={companyLabel}
                  dictionaries={dictionaries}
                  tableTaken={doc.fields.some((x, k) => k !== i && x.type === "table")}
                  onChange={(next, shifts) => setField(i, next, shifts)}
                  onMove={(delta) => update((d) => ({ ...d, fields: move(d.fields, i, delta) }), true)}
                  onDelete={() => update((d) => removeField(d, i), true)}
                  onAddColumn={() => addColumn(i)}
                  onDeleteColumn={(j) => update((d) => removeColumn(d, i, j), true)}
                  blockers={deleteBlockers(doc, i)}
                  columnBlockers={f.columns.map((_, j) => deleteBlockers(doc, i, j))}
                />
              ))}
            </ol>
          ) : (
            <p className="border-2 border-ink bg-card p-4 text-muted">Полей нет.</p>
          )}
          <div>
            <Button variant="ghost" onClick={addField}>
              Добавить поле
            </Button>
          </div>
        </section>
      </fieldset>

      <div className="flex flex-col gap-3 border-t-2 border-ink pt-4">
        <p role="status" aria-live="polite" className="font-mono text-sm">
          Изменения: {summary}
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <Button onClick={save} disabled={pending || summary === NO_CHANGES} aria-busy={pending}>
            {pending ? "Сохраняем…" : "Сохранить"}
          </Button>
          <Link
            href={cancelHref}
            className={`inline-flex min-h-11 items-center font-medium ${LINK}`}
            onClick={(e) => {
              if (dirty && !window.confirm("Уйти без сохранения? Правки пропадут.")) e.preventDefault();
            }}
          >
            Отмена
          </Link>
        </div>
      </div>
    </div>
  );
}
