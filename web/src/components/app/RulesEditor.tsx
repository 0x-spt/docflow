"use client";

import Link from "next/link";
import { useId, useRef, useState, useTransition } from "react";

import { saveRules } from "@/app/app/rules/actions";
import { Button, Field } from "@/components/ui";
import type { RoutingRule, RuleCondition, RuleOp } from "@/lib/types";

export type RuleSchema = { slug: string; title: string; fields: { name: string; label: string }[] };
export type RuleTarget = {
  id: string;
  name: string;
  system: string;
  canExport: boolean;
  canNotify: boolean;
  active: boolean;
};
type DictRef = { name: string; title: string };

const OPS: { op: RuleOp; label: string }[] = [
  { op: "eq", label: "равно" },
  { op: "ne", label: "не равно" },
  { op: "gt", label: "больше" },
  { op: "ge", label: "больше или равно" },
  { op: "lt", label: "меньше" },
  { op: "le", label: "меньше или равно" },
  { op: "contains", label: "содержит" },
  { op: "in_dictionary", label: "есть в справочнике" },
  { op: "empty", label: "пусто" },
  { op: "not_empty", label: "не пусто" },
];

type Row = { key: number; rule: RoutingRule };

const BTN =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text disabled:opacity-50 disabled:no-underline";
const CONTROL = "min-h-11 w-full border-2 border-ink bg-card px-3 py-2 text-base text-ink";
const CHECK = "size-5 shrink-0 accent-ink";

function noValue(op: RuleOp): boolean {
  return op === "empty" || op === "not_empty";
}

function emptyRule(): RoutingRule {
  return { name: "", schema: null, conditions: [], export_to: [], require_approval: false, notify: [] };
}

function TargetChecks({
  legend,
  empty,
  items,
  checked,
  onToggle,
}: {
  legend: string;
  empty: string;
  items: RuleTarget[];
  checked: string[];
  onToggle: (id: string, on: boolean) => void;
}) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className="mb-1 font-mono text-sm font-medium">{legend}</legend>
      {items.length === 0 ? (
        <p className="text-muted">{empty}</p>
      ) : (
        items.map((t) => (
          <label key={t.id} className="flex min-h-11 items-center gap-3">
            <input
              type="checkbox"
              className={CHECK}
              checked={checked.includes(t.id)}
              onChange={(e) => onToggle(t.id, e.target.checked)}
            />
            <span className="min-w-0 break-words">
              {t.name}{" "}
              <span className="text-muted">
                ({t.system}
                {t.active ? "" : ", отключено"})
              </span>
            </span>
          </label>
        ))
      )}
    </fieldset>
  );
}

export function RulesEditor({
  initial,
  schemas,
  targets,
  dictionaries,
  readOnly,
}: {
  initial: RoutingRule[];
  schemas: RuleSchema[];
  targets: RuleTarget[];
  dictionaries: DictRef[];
  readOnly: boolean;
}) {
  const nextKey = useRef(initial.length);
  const [rows, setRows] = useState<Row[]>(() => initial.map((rule, key) => ({ key, rule })));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [pending, start] = useTransition();
  const listId = useId();

  function touch(next: Row[]) {
    setRows(next);
    setSaved(false);
    setError(null);
  }
  const patch = (key: number, p: Partial<RoutingRule>) =>
    touch(rows.map((r) => (r.key === key ? { ...r, rule: { ...r.rule, ...p } } : r)));
  function move(i: number, d: -1 | 1) {
    const next = [...rows];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    touch(next);
  }
  function add() {
    touch([...rows, { key: nextKey.current++, rule: emptyRule() }]);
  }
  function submit() {
    setError(null);
    setSaved(false);
    start(async () => {
      const res = await saveRules(rows.map((r) => r.rule));
      if (res.error) setError(res.error);
      else setSaved(true);
    });
  }

  const allFields = [...new Map(schemas.flatMap((s) => s.fields).map((f) => [f.name, f])).values()];

  if (readOnly && rows.length === 0) {
    return <p className="border-2 border-ink bg-card p-4">Правил нет: документы никуда не выгружаются.</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      <datalist id={`${listId}-fields`}>
        {allFields.map((f) => (
          <option key={f.name} value={f.name}>
            {f.label}
          </option>
        ))}
      </datalist>

      {rows.length === 0 ? (
        <div className="border-2 border-ink bg-card p-6">
          <p className="text-lg font-medium">Правил пока нет.</p>
          <p className="mt-1 text-muted">
            Без правил документы никуда не выгружаются. Добавьте правило и выберите, куда отправлять документы. Системы
            добавляются на странице{" "}
            <Link href="/app/integrations" className="underline decoration-2 underline-offset-4">
              «Подключения»
            </Link>
            .
          </p>
        </div>
      ) : (
        <ol className="flex flex-col gap-4">
          {rows.map(({ key, rule }, i) => {
            const p = `rule${key}`;
            const schema = schemas.find((s) => s.slug === rule.schema);
            const fieldOptions = schema ? schema.fields : null;
            const exportTargets = targets.filter((t) => t.canExport || rule.export_to.includes(t.id));
            const notifyTargets = targets.filter((t) => t.canNotify || rule.notify.includes(t.id));
            const setCond = (ci: number, c: Partial<RuleCondition>) =>
              patch(key, { conditions: rule.conditions.map((x, j) => (j === ci ? { ...x, ...c } : x)) });
            const toggle = (list: "export_to" | "notify", id: string, on: boolean) =>
              patch(key, { [list]: on ? [...rule[list], id] : rule[list].filter((x) => x !== id) });
            return (
              <li key={key} className="border-2 border-ink bg-card p-4">
                <fieldset disabled={readOnly || pending} className="m-0 flex min-w-0 flex-col gap-4 border-0 p-0">
                  <legend className="mb-2 font-mono text-sm text-muted">Правило {i + 1}</legend>
                  <div className="grid gap-4 md:grid-cols-2">
                    <Field
                      label="Название"
                      name={`${p}-name`}
                      value={rule.name}
                      maxLength={200}
                      onChange={(e) => patch(key, { name: e.target.value })}
                    />
                    <Field
                      as="select"
                      label="Тип документа"
                      name={`${p}-schema`}
                      value={rule.schema ?? ""}
                      onChange={(e) => patch(key, { schema: e.target.value || null })}
                    >
                      <option value="">Любой</option>
                      {rule.schema && !schema ? <option value={rule.schema}>{rule.schema}</option> : null}
                      {schemas.map((s) => (
                        <option key={s.slug} value={s.slug}>
                          {s.title}
                        </option>
                      ))}
                    </Field>
                  </div>

                  <div className="flex flex-col gap-2">
                    <p className="font-mono text-sm font-medium">Условия (выполнены все сразу)</p>
                    {rule.conditions.length === 0 ? (
                      <p className="text-muted">Без условий: правило подходит каждому документу этого типа.</p>
                    ) : null}
                    {rule.conditions.map((c, ci) => {
                      const cp = `${p}-c${ci}`;
                      return (
                        <div
                          key={ci}
                          className="grid items-end gap-2 border-t border-ink/30 pt-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
                        >
                          <div className="flex flex-col gap-1.5">
                            <label htmlFor={`${cp}-field`} className="font-mono text-sm font-medium">
                              Поле
                            </label>
                            {fieldOptions ? (
                              <select
                                id={`${cp}-field`}
                                value={c.field}
                                onChange={(e) => setCond(ci, { field: e.target.value })}
                                className={CONTROL}
                              >
                                <option value="">— выберите —</option>
                                {c.field && !fieldOptions.some((f) => f.name === c.field) ? (
                                  <option value={c.field}>{c.field}</option>
                                ) : null}
                                {fieldOptions.map((f) => (
                                  <option key={f.name} value={f.name}>
                                    {f.label} ({f.name})
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <input
                                id={`${cp}-field`}
                                value={c.field}
                                list={`${listId}-fields`}
                                autoComplete="off"
                                onChange={(e) => setCond(ci, { field: e.target.value })}
                                className={CONTROL}
                              />
                            )}
                          </div>
                          <div className="flex flex-col gap-1.5">
                            <label htmlFor={`${cp}-op`} className="font-mono text-sm font-medium">
                              Условие
                            </label>
                            <select
                              id={`${cp}-op`}
                              value={c.op}
                              onChange={(e) => {
                                const op = e.target.value as RuleOp;
                                setCond(ci, { op, value: noValue(op) ? null : "" });
                              }}
                              className={CONTROL}
                            >
                              {OPS.map((o) => (
                                <option key={o.op} value={o.op}>
                                  {o.label}
                                </option>
                              ))}
                            </select>
                          </div>
                          <div className="flex flex-col gap-1.5">
                            {noValue(c.op) ? (
                              <span className="min-h-11 py-2 text-muted">Значение не нужно</span>
                            ) : (
                              <>
                                <label htmlFor={`${cp}-value`} className="font-mono text-sm font-medium">
                                  {c.op === "in_dictionary" ? "Справочник" : "Значение"}
                                </label>
                                {c.op === "in_dictionary" ? (
                                  <select
                                    id={`${cp}-value`}
                                    value={String(c.value ?? "")}
                                    onChange={(e) => setCond(ci, { value: e.target.value })}
                                    className={CONTROL}
                                  >
                                    <option value="">— выберите —</option>
                                    {dictionaries.map((d) => (
                                      <option key={d.name} value={d.name}>
                                        {d.title || d.name}
                                      </option>
                                    ))}
                                  </select>
                                ) : (
                                  <input
                                    id={`${cp}-value`}
                                    value={String(c.value ?? "")}
                                    autoComplete="off"
                                    onChange={(e) => setCond(ci, { value: e.target.value })}
                                    className={CONTROL}
                                  />
                                )}
                              </>
                            )}
                          </div>
                          <button
                            type="button"
                            className={BTN}
                            aria-label={`Убрать условие ${ci + 1} правила ${i + 1}`}
                            onClick={() => patch(key, { conditions: rule.conditions.filter((_, j) => j !== ci) })}
                          >
                            Убрать
                          </button>
                        </div>
                      );
                    })}
                    <div>
                      <button
                        type="button"
                        className={BTN}
                        onClick={() =>
                          patch(key, { conditions: [...rule.conditions, { field: "", op: "eq", value: "" }] })
                        }
                      >
                        Добавить условие
                      </button>
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <TargetChecks
                      legend="Выгрузить в"
                      empty="Нет подходящих подключений."
                      items={exportTargets}
                      checked={rule.export_to}
                      onToggle={(id, on) => toggle("export_to", id, on)}
                    />
                    <TargetChecks
                      legend="Уведомить"
                      empty="Нет подходящих подключений (Telegram, почта)."
                      items={notifyTargets}
                      checked={rule.notify}
                      onToggle={(id, on) => toggle("notify", id, on)}
                    />
                  </div>

                  <label className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      className={`${CHECK} mt-3`}
                      checked={rule.require_approval}
                      onChange={(e) => patch(key, { require_approval: e.target.checked })}
                    />
                    <span className="py-2">
                      Требовать согласования
                      <span className="block text-sm text-muted">
                        Документ сначала проверит человек, и только потом он уйдёт в системы.
                      </span>
                    </span>
                  </label>

                  {!readOnly ? (
                    <div className="flex flex-wrap items-center gap-x-2 border-t-2 border-ink pt-2">
                      <button
                        type="button"
                        className={BTN}
                        disabled={i === 0}
                        aria-label={`Поднять правило ${i + 1} выше`}
                        onClick={() => move(i, -1)}
                      >
                        ↑ Выше
                      </button>
                      <button
                        type="button"
                        className={BTN}
                        disabled={i === rows.length - 1}
                        aria-label={`Опустить правило ${i + 1} ниже`}
                        onClick={() => move(i, 1)}
                      >
                        ↓ Ниже
                      </button>
                      <button
                        type="button"
                        className={`${BTN} text-accent-text`}
                        aria-label={`Удалить правило ${i + 1}`}
                        onClick={() => touch(rows.filter((r) => r.key !== key))}
                      >
                        Удалить правило
                      </button>
                    </div>
                  ) : null}
                </fieldset>
              </li>
            );
          })}
        </ol>
      )}

      {!readOnly ? (
        <div className="flex flex-wrap items-center gap-4">
          <Button variant="ghost" onClick={add} disabled={pending || rows.length >= 100}>
            Добавить правило
          </Button>
          <Button onClick={submit} disabled={pending} aria-busy={pending}>
            {pending ? "Сохраняем…" : "Сохранить правила"}
          </Button>
        </div>
      ) : null}
      <div role="status" aria-live="polite">
        {saved ? <p className="border-2 border-ink bg-card p-3 font-medium">Правила сохранены.</p> : null}
      </div>
      {error ? (
        <div role="alert" className="border-2 border-accent-text bg-paper p-3 font-medium text-accent-text">
          {error}
        </div>
      ) : null}
    </div>
  );
}
