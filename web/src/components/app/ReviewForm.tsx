"use client";

import Link from "next/link";
import { startTransition, useActionState, useEffect, useRef, useState, type FormEvent } from "react";

import { approve, reject, type ReviewState } from "@/app/app/documents/[id]/actions";
import { Button, Field } from "@/components/ui";
import { fieldLabel, valueToInput } from "@/lib/format";
import { cellName, rowsName, scalarName, type FieldNotes } from "@/lib/review-form";
import type { DocSchema, SchemaColumn, SchemaField } from "@/lib/types";

const INITIAL: ReviewState = {};
const CONTROL =
  "min-h-11 w-full border-2 border-ink bg-card px-2 py-1.5 font-sans text-base text-ink " +
  "aria-[invalid=true]:border-accent-text aria-[invalid=true]:bg-paper";

function placeholder(spec: { type: string }): string | undefined {
  if (spec.type === "date") return "ДД.ММ.ГГГГ";
  return undefined;
}

function inputMode(spec: { type: string }) {
  if (spec.type === "number") return "decimal" as const;
  if (spec.type === "integer") return "numeric" as const;
  return undefined;
}

function ScalarField({ spec, value, notes }: { spec: SchemaField; value: unknown; notes?: FieldNotes[string] }) {
  const name = scalarName(spec.name);
  const label = fieldLabel(spec) + (spec.required ? " *" : "");
  const error = notes?.problems.join("; ") || undefined;
  const hint = notes?.info.join("; ") || undefined;

  if (spec.type === "boolean") {
    return (
      <div className="flex flex-col gap-1.5">
        <label className="inline-flex min-h-11 items-center gap-3 font-mono text-sm font-medium">
          <input
            type="checkbox"
            name={name}
            value="true"
            defaultChecked={value === true}
            aria-invalid={error ? true : undefined}
            className="size-5 accent-ink"
          />
          {label}
        </label>
        {hint ? <p className="text-sm text-muted">{hint}</p> : null}
        {error ? <p className="text-sm font-medium text-accent-text">{error}</p> : null}
      </div>
    );
  }
  if (spec.type === "enum") {
    const current = typeof value === "string" ? value : "";
    const values = spec.values ?? [];
    return (
      <Field as="select" label={label} name={name} defaultValue={current} error={error} hint={hint}>
        <option value="">—</option>
        {/* значение вне списка (старый документ) не теряем: показываем и даём выбрать */}
        {current && !values.includes(current) ? <option value={current}>{current}</option> : null}
        {values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </Field>
    );
  }
  return (
    <Field
      label={label}
      name={name}
      defaultValue={valueToInput(spec, value)}
      placeholder={placeholder(spec)}
      inputMode={inputMode(spec)}
      autoComplete="off"
      error={error}
      hint={hint}
      className={spec.type === "string" ? "" : "font-mono tabular-nums"}
    />
  );
}

function Cell({
  table,
  row,
  rowNumber,
  column,
  value,
  notes,
}: {
  table: string;
  row: string;
  rowNumber: number;
  column: SchemaColumn;
  value: unknown;
  notes?: FieldNotes[string];
}) {
  const name = cellName(table, row, column.name);
  const label = `${fieldLabel(column)}, строка ${rowNumber}`;
  const problem = notes?.problems.join("; ");
  const info = notes?.info.join("; ");
  const title = [problem, info].filter(Boolean).join("; ") || undefined;

  if (column.type === "boolean") {
    return (
      <input
        type="checkbox"
        name={name}
        value="true"
        defaultChecked={value === true}
        aria-label={label}
        className="size-5 accent-ink"
      />
    );
  }
  if (column.type === "enum") {
    const current = typeof value === "string" ? value : "";
    const values = column.values ?? [];
    return (
      <select name={name} defaultValue={current} aria-label={label} aria-invalid={problem ? true : undefined} title={title} className={CONTROL}>
        <option value="">—</option>
        {current && !values.includes(current) ? <option value={current}>{current}</option> : null}
        {values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      name={name}
      defaultValue={valueToInput(column, value)}
      placeholder={placeholder(column)}
      inputMode={inputMode(column)}
      autoComplete="off"
      aria-label={label}
      aria-invalid={problem ? true : undefined}
      title={title}
      className={`${CONTROL} ${column.type === "string" ? "" : "font-mono tabular-nums"}`}
    />
  );
}

function TableField({ spec, value, notes }: { spec: SchemaField; value: unknown; notes: FieldNotes }) {
  const initial = Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
  // ключ строки: исходный индекс (по нему привязаны замечания «works[0].price») или «nN» для новых
  const [rows, setRows] = useState(() => initial.map((_, i) => String(i)));
  const added = useRef(0);
  const columns = spec.columns ?? [];
  const own = notes[spec.name];
  const headingId = `t-${spec.name}`;

  const rowValue = (key: string) => (/^\d+$/.test(key) ? initial[Number(key)] : undefined);
  const cellNotes = (key: string, column: string) =>
    /^\d+$/.test(key) ? notes[`${spec.name}[${key}].${column}`] : undefined;

  return (
    <fieldset className="flex min-w-0 flex-col gap-2 sm:col-span-2" aria-labelledby={headingId}>
      <div id={headingId} className="font-mono text-sm font-medium">
        {fieldLabel(spec)}
        {spec.required ? " *" : ""}
      </div>
      {own?.info.length ? <p className="text-sm text-muted">{own.info.join("; ")}</p> : null}
      {own?.problems.length ? (
        <p className="text-sm font-medium text-accent-text">{own.problems.join("; ")}</p>
      ) : null}
      <div className="relative overflow-x-auto border-2 border-ink bg-card">
        <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
          <thead className="bg-paper">
            <tr className="border-b-2 border-ink">
              <th scope="col" className="w-10 px-2 py-2 font-mono font-medium">
                №
              </th>
              {columns.map((c) => (
                <th
                  key={c.name}
                  scope="col"
                  className={`px-2 py-2 font-mono font-medium ${c.type === "string" ? "min-w-44" : "min-w-24"}`}
                >
                  {fieldLabel(c)}
                </th>
              ))}
              <th scope="col" className="w-10 px-2 py-2">
                <span className="sr-only">Удалить</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((key, i) => {
              const row = rowValue(key);
              return (
                <tr key={key} className="border-t border-ink/30 align-top">
                  <td className="px-2 py-1.5 font-mono tabular-nums">
                    <input type="hidden" name={rowsName(spec.name)} value={key} />
                    <span className="inline-flex min-h-11 items-center">{i + 1}</span>
                  </td>
                  {columns.map((c) => (
                    <td key={c.name} className="px-1 py-1.5">
                      <Cell
                        table={spec.name}
                        row={key}
                        rowNumber={i + 1}
                        column={c}
                        value={row?.[c.name]}
                        notes={cellNotes(key, c.name)}
                      />
                    </td>
                  ))}
                  <td className="px-1 py-1.5">
                    <button
                      type="button"
                      onClick={() => setRows((r) => r.filter((k) => k !== key))}
                      aria-label={`Удалить строку ${i + 1}`}
                      className="inline-flex size-11 items-center justify-center font-mono text-lg hover:text-accent-text"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {/* замечания по ячейкам — видимым текстом под таблицей, с номером строки */}
      {rows.some((key) => columns.some((c) => cellNotes(key, c.name)?.problems.length)) ? (
        <ul className="flex flex-col gap-1 text-sm font-medium text-accent-text">
          {rows.flatMap((key, i) =>
            columns.flatMap((c) =>
              (cellNotes(key, c.name)?.problems ?? []).map((m, j) => (
                <li key={`${key}-${c.name}-${j}`}>
                  Строка {i + 1}, {fieldLabel(c).toLowerCase()}: {m}
                </li>
              )),
            ),
          )}
        </ul>
      ) : null}
      <div>
        <button
          type="button"
          onClick={() => setRows((r) => [...r, `n${++added.current}`])}
          className="inline-flex min-h-11 items-center px-1 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text"
        >
          Добавить строку
        </button>
      </div>
    </fieldset>
  );
}

/** Форма проверки: поля по схеме, комментарий, «Подтвердить и отправить» / «Отклонить». */
export function ReviewForm({
  id,
  schema,
  data,
  notes,
}: {
  id: string;
  schema: DocSchema;
  data: Record<string, unknown>;
  notes: FieldNotes;
}) {
  const [approveState, approveAction, approving] = useActionState(approve.bind(null, id), INITIAL);
  const [rejectState, rejectAction, rejecting] = useActionState(reject.bind(null, id), INITIAL);
  const [last, setLast] = useState<"approve" | "reject" | null>(null);
  const state = last === "reject" ? rejectState : approveState;
  const pending = approving || rejecting;
  const alertRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (state.error || state.conflict) alertRef.current?.focus();
  }, [state]);

  // отправка вручную: автосброс формы React 19 стёр бы правки оператора при ошибке
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const decision = submitter?.value === "reject" ? "reject" : "approve";
    const form = new FormData(event.currentTarget);
    setLast(decision);
    startTransition(() => (decision === "reject" ? rejectAction(form) : approveAction(form)));
  }

  const scalars = schema.fields.filter((f) => f.type !== "table");
  const tables = schema.fields.filter((f) => f.type === "table");

  return (
    <form onSubmit={onSubmit} aria-busy={pending} className="flex flex-col gap-8">
      <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
        {scalars.map((f) => (
          <ScalarField key={f.name} spec={f} value={data[f.name]} notes={notes[f.name]} />
        ))}
        {tables.map((f) => (
          <TableField key={f.name} spec={f} value={data[f.name]} notes={notes} />
        ))}
      </div>

      <div className="flex flex-col gap-4 border-2 border-ink bg-card p-4 shadow-hard sm:p-5">
        {state.conflict ? (
          <div ref={alertRef} tabIndex={-1} role="alert" className="border-2 border-accent-text bg-paper p-4">
            <p className="font-medium text-accent-text">Документ уже проверен</p>
            <p className="mt-1">
              Его проверил другой оператор или он снова в обработке.{" "}
              <Link href="/app" className="font-medium underline decoration-2 underline-offset-4 hover:text-accent-text">
                Вернуться в очередь
              </Link>
            </p>
          </div>
        ) : state.error ? (
          <div
            ref={alertRef}
            tabIndex={-1}
            role="alert"
            className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text"
          >
            {state.error}
          </div>
        ) : null}
        <Field
          as="textarea"
          rows={2}
          label="Комментарий"
          name="note"
          maxLength={1000}
          hint="Для отклонения обязателен: причина уйдёт в вебхук и останется в карточке документа."
        />
        <div className="flex flex-col gap-4 sm:flex-row">
          <Button type="submit" name="decision" value="approve" variant="primary" disabled={pending || state.conflict}>
            {approving ? "Отправляем…" : "Подтвердить и отправить"}
          </Button>
          <Button type="submit" name="decision" value="reject" variant="ghost" disabled={pending || state.conflict}>
            {rejecting ? "Отклоняем…" : "Отклонить"}
          </Button>
        </div>
      </div>
    </form>
  );
}
