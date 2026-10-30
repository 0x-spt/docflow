"use client";

import { useMemo, useState } from "react";

import { Button, Field } from "@/components/ui";
import { toSchema, type EditorDoc } from "@/lib/field-builder/model";
import { DOC_RULE_PARAMS, accountBikRule, rowProductRule, rulePhrase, sumRule } from "@/lib/field-builder/rules";
import type { RuleSpec, SchemaField } from "@/lib/types";

const BTN =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text disabled:opacity-50 disabled:no-underline";
const NUMERIC = new Set(["number", "integer"]);

interface Opt {
  value: string;
  label: string;
}

/** Совпадают ли два правила по виду и значимым параметрам (допуск и прочее не в счёт). */
function sameRule(a: RuleSpec, b: RuleSpec): boolean {
  const spec = DOC_RULE_PARAMS[a.kind];
  if (a.kind !== b.kind || !spec) return false;
  return Object.keys(spec).every((p) => a.params[p] === b.params[p]);
}

/** Одна форма добавления: <details> с селектами или причиной, почему форма недоступна. */
function AddForm({
  id,
  summary,
  unavailable,
  selects,
  onAdd,
  notice,
}: {
  id: string;
  summary: string;
  unavailable: string | null;
  selects: { name: string; label: string; options: Opt[]; value: string; set(v: string): void }[];
  onAdd(): void;
  notice: string | null;
}) {
  return (
    <details className="border-2 border-ink bg-card p-3">
      <summary className="min-h-11 cursor-pointer py-2 font-medium">{summary}</summary>
      {unavailable ? (
        <p className="mt-2 text-sm text-muted">{unavailable}</p>
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          {selects.map((s) => (
            <Field
              key={s.name}
              as="select"
              label={s.label}
              name={`${id}-${s.name}`}
              value={s.value}
              onChange={(e) => s.set(e.target.value)}
            >
              {s.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Field>
          ))}
          {notice ? (
            <p role="alert" className="text-sm font-medium text-accent-text">
              {notice}
            </p>
          ) : null}
          <div>
            <Button variant="ghost" onClick={onAdd}>
              Добавить
            </Button>
          </div>
        </div>
      )}
    </details>
  );
}

/** Выбранное значение, если оно ещё среди вариантов, иначе вариант по умолчанию. */
const pick = (chosen: string | undefined, opts: Opt[], fallback = 0): string =>
  chosen !== undefined && opts.some((o) => o.value === chosen) ? chosen : (opts[fallback]?.value ?? opts[0]?.value ?? "");

const NO_COLS: Opt[] = [];

/** Правила документа: фразы, ошибки сервера, удаление и три добавления. */
export function DocRulesList({
  doc,
  titleOf,
  errors,
  onChange,
}: {
  doc: EditorDoc;
  titleOf: (key: string) => string;
  errors: Record<string, string>;
  onChange(rules: RuleSpec[]): void;
}) {
  const schema = useMemo(() => toSchema(doc), [doc]);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<{ form: string; text: string } | null>(null);
  const set = (k: string) => (v: string) => {
    setChosen((c) => ({ ...c, [k]: v }));
    setNotice(null);
  };

  const named = schema.fields.filter((f) => f.name);
  const label = (f: SchemaField): string => titleOf(f.name);
  const colOpts = (t: SchemaField | undefined): Opt[] =>
    t
      ? (t.columns ?? [])
          .filter((c) => c.name && NUMERIC.has(c.type))
          .map((c) => ({ value: c.name, label: titleOf(`${t.name}.${c.name}`) }))
      : NO_COLS;
  const tables = named.filter((f) => f.type === "table");
  const numFields: Opt[] = named.filter((f) => NUMERIC.has(f.type)).map((f) => ({ value: f.name, label: label(f) }));
  const textFields = named.filter((f) => f.type === "string");
  const textOpts: Opt[] = textFields.map((f) => ({ value: f.name, label: label(f) }));

  function add(form: string, rule: RuleSpec) {
    if (doc.rules.some((r) => sameRule(r, rule))) {
      setNotice({ form, text: "Такое правило уже есть" });
      return;
    }
    setNotice(null);
    onChange([...doc.rules, rule]);
  }

  // 1. сумма колонки = поле
  const sumTables = tables.filter((t) => colOpts(t).length > 0);
  const sumTableOpts: Opt[] = sumTables.map((t) => ({ value: t.name, label: label(t) }));
  const sumTable = pick(chosen.sumTable, sumTableOpts);
  const sumCols = colOpts(sumTables.find((t) => t.name === sumTable));
  const sumCol = pick(chosen.sumCol, sumCols);
  const sumTotal = pick(chosen.sumTotal, numFields);

  // 2. кол-во × цена = сумма в строке
  const rowTables = tables.filter((t) => colOpts(t).length >= 3);
  const rowTableOpts: Opt[] = rowTables.map((t) => ({ value: t.name, label: label(t) }));
  const rowTable = pick(chosen.rowTable, rowTableOpts);
  const rowCols = colOpts(rowTables.find((t) => t.name === rowTable));
  const qty = pick(chosen.qty, rowCols, 0);
  const price = pick(chosen.price, rowCols, 1);
  const amount = pick(chosen.amount, rowCols, 2);

  // 3. счёт сходится с БИК: по умолчанию БИК — поле с проверкой bik
  const bikAt = Math.max(
    0,
    textFields.findIndex((f) => (f.rules ?? []).some((r) => r.kind === "bik")),
  );
  const bik = pick(chosen.bik, textOpts, bikAt);
  const account = pick(chosen.account, textOpts, bikAt === 0 ? 1 : 0);

  const note = (form: string) => (notice?.form === form ? notice.text : null);

  return (
    <section aria-labelledby="schema-rules" className="flex flex-col gap-4">
      <h2 id="schema-rules" className="text-2xl font-semibold tracking-tight">
        Правила проверки документа
      </h2>
      {doc.rules.length ? (
        <ul className="flex flex-col gap-3">
          {doc.rules.map((r, j) => {
            const phrase = rulePhrase(r, titleOf);
            const err = errors[`rules.${j}`];
            return (
              <li key={j} className="flex flex-col gap-1 border-2 border-ink bg-card p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0 break-words">{phrase}</span>
                  <button
                    type="button"
                    className={BTN}
                    onClick={() => {
                      if (window.confirm(`Удалить правило «${phrase}»?`)) onChange(doc.rules.filter((_, k) => k !== j));
                    }}
                  >
                    Удалить правило
                  </button>
                </div>
                {err ? (
                  <p role="alert" className="text-sm font-medium text-accent-text">
                    {err}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="border-2 border-ink bg-card p-4 text-muted">Правил нет.</p>
      )}

      <div className="flex flex-col gap-3">
        <AddForm
          id="rule-sum"
          summary="Сумма колонки = поле"
          unavailable={sumTables.length && numFields.length ? null : "Нужна таблица с числовыми колонками и числовое поле"}
          selects={[
            { name: "table", label: "Таблица", options: sumTableOpts, value: sumTable, set: set("sumTable") },
            { name: "col", label: "Колонка", options: sumCols, value: sumCol, set: set("sumCol") },
            { name: "total", label: "Поле", options: numFields, value: sumTotal, set: set("sumTotal") },
          ]}
          onAdd={() => add("sum", sumRule(sumTable, sumCol, sumTotal))}
          notice={note("sum")}
        />
        <AddForm
          id="rule-row"
          summary="Кол-во × цена = сумма в строке"
          unavailable={rowTables.length ? null : "Нужны три числовые колонки таблицы"}
          selects={[
            { name: "table", label: "Таблица", options: rowTableOpts, value: rowTable, set: set("rowTable") },
            { name: "qty", label: "Количество", options: rowCols, value: qty, set: set("qty") },
            { name: "price", label: "Цена", options: rowCols, value: price, set: set("price") },
            { name: "amount", label: "Сумма", options: rowCols, value: amount, set: set("amount") },
          ]}
          onAdd={() => {
            if (new Set([qty, price, amount]).size < 3) {
              setNotice({ form: "row", text: "Выберите три разные колонки" });
              return;
            }
            add("row", rowProductRule(rowTable, qty, price, amount));
          }}
          notice={note("row")}
        />
        <AddForm
          id="rule-bik"
          summary="Счёт сходится с БИК"
          unavailable={textFields.length >= 2 ? null : "Нужны два текстовых поля"}
          selects={[
            { name: "account", label: "Счёт", options: textOpts, value: account, set: set("account") },
            { name: "bik", label: "БИК", options: textOpts, value: bik, set: set("bik") },
          ]}
          onAdd={() => {
            if (account === bik) {
              setNotice({ form: "bik", text: "Выберите два разных поля" });
              return;
            }
            add("bik", accountBikRule(account, bik));
          }}
          notice={note("bik")}
        />
      </div>
    </section>
  );
}
