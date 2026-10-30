"use client";

import { useState } from "react";

import { Field } from "@/components/ui";
import {
  CHECK_OPTIONS,
  TYPE_OPTIONS,
  move,
  type CheckKind,
  type EditorColumn,
  type EditorField,
} from "@/lib/field-builder/model";
import type { ColumnType, FieldType } from "@/lib/types";

type DictRef = { name: string; title: string };

const BTN =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text disabled:opacity-50 disabled:no-underline";
const CHECKBOX = "size-5 shrink-0 accent-ink";
const ALERT = "border-2 border-accent-text bg-paper p-3 text-sm font-medium text-accent-text";

// без них документ в очереди и выгрузках теряет номер и дату
const SPECIAL: Record<string, string> = {
  number: " Без него документ в очереди и выгрузках будет без номера.",
  issued: " Без него документ в очереди и выгрузках будет без даты.",
};

const shown = (row: { title: string; name: string }): string => row.title.trim() || row.name;

function blockedText(what: "Поле" | "Колонка", phrases: string[]): string {
  const list = phrases.map((p) => `«${p}»`).join(", ");
  return phrases.length > 1
    ? `${what} используется в правилах ${list}. Сначала удалите правила.`
    : `${what} используется в правиле ${list}. Сначала удалите правило.`;
}

/** Общая разметка поля и колонки: название, служебное имя, подпись компании, подсказка, тип, варианты. */
function RowBasics({
  row,
  idBase,
  labelKey,
  errPrefix,
  errors,
  companyLabel,
  typeOptions,
  typeDisabled,
  onTitle,
  onDescription,
  onType,
  onValues,
}: {
  row: { name: string; title: string; description: string; type: FieldType; values: string };
  idBase: string;
  labelKey: string;
  errPrefix: string;
  errors: Record<string, string>;
  companyLabel: (key: string) => string | undefined;
  typeOptions: { value: FieldType; label: string }[];
  typeDisabled: (t: FieldType) => boolean;
  onTitle: (v: string) => void;
  onDescription: (v: string) => void;
  onType: (v: FieldType) => void;
  onValues: (v: string) => void;
}) {
  const company = companyLabel(labelKey);
  return (
    <>
      <div className="flex flex-col gap-1">
        <Field
          label="Название"
          name={`${idBase}-title`}
          value={row.title}
          placeholder={row.name}
          maxLength={200}
          autoComplete="off"
          onChange={(e) => onTitle(e.target.value)}
          error={errors[`${errPrefix}.title`]}
        />
        <p className="font-mono text-xs text-muted">{row.name}</p>
        {company ? (
          <p className="text-sm text-muted">
            Название компании: «{company}» — главнее, меняется на странице «Поля»
          </p>
        ) : null}
      </div>
      <Field
        as="textarea"
        rows={2}
        label="Подсказка для модели"
        name={`${idBase}-description`}
        value={row.description}
        onChange={(e) => onDescription(e.target.value)}
        error={errors[`${errPrefix}.description`]}
      />
      <Field
        as="select"
        label="Тип"
        name={`${idBase}-type`}
        value={row.type}
        onChange={(e) => onType(e.target.value as FieldType)}
        error={errors[`${errPrefix}.type`]}
      >
        {typeOptions.map((o) => (
          <option key={o.value} value={o.value} disabled={typeDisabled(o.value)}>
            {o.label}
          </option>
        ))}
      </Field>
      {row.type === "enum" ? (
        <Field
          as="textarea"
          rows={4}
          label="Варианты (по одному в строке)"
          name={`${idBase}-values`}
          value={row.values}
          onChange={(e) => onValues(e.target.value)}
          error={errors[`${errPrefix}.values`]}
        />
      ) : null}
    </>
  );
}

const COLUMN_TYPES = TYPE_OPTIONS.filter((o) => o.value !== "table");

/** Карточка поля верхнего уровня; у таблицы — её колонки. */
export function SchemaFieldCard({
  field,
  index,
  count,
  errors,
  companyLabel,
  dictionaries,
  tableTaken,
  onChange,
  onMove,
  onDelete,
  onAddColumn,
  onDeleteColumn,
  blockers,
  columnBlockers,
}: {
  field: EditorField;
  index: number;
  count: number;
  errors: Record<string, string>;
  companyLabel: (key: string) => string | undefined;
  dictionaries: DictRef[];
  /** Таблица в типе уже есть (у другого поля) — «Таблица» недоступна. */
  tableTaken: boolean;
  onChange: (f: EditorField) => void;
  onMove: (delta: -1 | 1) => void;
  onDelete: () => void;
  onAddColumn: () => void;
  onDeleteColumn: (columnIndex: number) => void;
  /** Фразы правил документа, которые ссылаются на поле. */
  blockers: string[];
  /** То же по колонкам таблицы, в порядке колонок. */
  columnBlockers: string[][];
}) {
  const [blocked, setBlocked] = useState<string | null>(null);
  const [colBlocked, setColBlocked] = useState<{ uid: string; text: string } | null>(null);
  const key = `fields.${index}`;
  const title = shown(field);
  const patch = (p: Partial<EditorField>) => onChange({ ...field, ...p });
  const patchColumn = (j: number, p: Partial<EditorColumn>) =>
    patch({ columns: field.columns.map((c, k) => (k === j ? { ...c, ...p } : c)) });

  function remove() {
    if (blockers.length) {
      setBlocked(blockedText("Поле", blockers));
      return;
    }
    setBlocked(null);
    if (window.confirm(`Удалить поле «${title}»?${SPECIAL[field.name] ?? ""}`)) onDelete();
  }

  function removeColumn(j: number) {
    const c = field.columns[j];
    const using = columnBlockers[j] ?? [];
    if (using.length) {
      setColBlocked({ uid: c.uid, text: blockedText("Колонка", using) });
      return;
    }
    setColBlocked(null);
    if (window.confirm(`Удалить колонку «${shown(c)}»?`)) onDeleteColumn(j);
  }

  // правило сняли — сообщение о нём больше не нужно
  const fieldAlerts = [errors[key], errors[`${key}.columns`], blockers.length ? blocked : null].filter(Boolean);
  const isTable = field.type === "table";
  const dictKnown = dictionaries.some((d) => d.name === field.checkParam);

  return (
    <li className="border-2 border-ink bg-card p-4">
      <fieldset className="m-0 flex min-w-0 flex-col gap-4 border-0 p-0">
        <legend className="mb-2 font-mono text-sm text-muted">Поле {index + 1}</legend>
        {fieldAlerts.length ? (
          <div role="alert" className={ALERT}>
            {fieldAlerts.map((t, i) => (
              <p key={i}>{t}</p>
            ))}
          </div>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2">
          <RowBasics
            row={field}
            idBase={field.uid}
            labelKey={field.name}
            errPrefix={key}
            errors={errors}
            companyLabel={companyLabel}
            typeOptions={TYPE_OPTIONS}
            typeDisabled={(t) => t === "table" && tableTaken}
            onTitle={(v) => patch({ title: v })}
            onDescription={(v) => patch({ description: v })}
            onType={(v) => patch({ type: v })}
            onValues={(v) => patch({ values: v })}
          />
        </div>

        <label className="flex min-h-11 items-center gap-3">
          <input
            type="checkbox"
            className={CHECKBOX}
            checked={field.required}
            onChange={(e) => patch({ required: e.target.checked })}
          />
          <span>Обязательное</span>
        </label>

        {field.type === "date" ? (
          <label className="flex min-h-11 items-center gap-3">
            <input
              type="checkbox"
              className={CHECKBOX}
              checked={field.notFuture}
              onChange={(e) => patch({ notFuture: e.target.checked })}
            />
            <span>Дата не в будущем</span>
          </label>
        ) : null}

        {!isTable ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Field
              as="select"
              label="Проверка"
              name={`${field.uid}-check`}
              value={field.check}
              onChange={(e) => patch({ check: e.target.value as CheckKind, checkParam: "" })}
            >
              {CHECK_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Field>
            {field.check === "dictionary" ? (
              <Field
                as="select"
                label="Справочник"
                name={`${field.uid}-dictionary`}
                value={field.checkParam}
                disabled={dictionaries.length === 0 && !field.checkParam}
                onChange={(e) => patch({ checkParam: e.target.value })}
                error={errors[`${key}.check`]}
              >
                {dictionaries.length === 0 && !field.checkParam ? (
                  <option value="">Справочников нет</option>
                ) : (
                  <option value="">— выберите —</option>
                )}
                {field.checkParam && !dictKnown ? (
                  <option value={field.checkParam}>{field.checkParam}</option>
                ) : null}
                {dictionaries.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.title || d.name}
                  </option>
                ))}
              </Field>
            ) : field.check === "regex" ? (
              <Field
                label="Шаблон"
                name={`${field.uid}-pattern`}
                value={field.checkParam}
                autoComplete="off"
                spellCheck={false}
                className="font-mono"
                onChange={(e) => patch({ checkParam: e.target.value })}
                error={errors[`${key}.check`]}
              />
            ) : null}
          </div>
        ) : null}

        {isTable ? (
          <div className="flex flex-col gap-3">
            <p className="font-mono text-sm font-medium">Колонки</p>
            {field.columns.length ? (
              <ol className="flex flex-col gap-3">
                {field.columns.map((c, j) => {
                  const ck = `${key}.columns.${j}`;
                  const colTitle = shown(c);
                  const alerts = [errors[ck], colBlocked?.uid === c.uid && columnBlockers[j]?.length ? colBlocked.text : null].filter(Boolean);
                  return (
                    <li key={c.uid} className="border-2 border-ink/40 bg-paper p-3">
                      <fieldset className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
                        <legend className="mb-1 font-mono text-xs text-muted">Колонка {j + 1}</legend>
                        {alerts.length ? (
                          <div role="alert" className={ALERT}>
                            {alerts.map((t, i) => (
                              <p key={i}>{t}</p>
                            ))}
                          </div>
                        ) : null}
                        <div className="grid gap-3 md:grid-cols-2">
                          <RowBasics
                            row={c}
                            idBase={c.uid}
                            labelKey={`${field.name}.${c.name}`}
                            errPrefix={ck}
                            errors={errors}
                            companyLabel={companyLabel}
                            typeOptions={COLUMN_TYPES}
                            typeDisabled={() => false}
                            onTitle={(v) => patchColumn(j, { title: v })}
                            onDescription={(v) => patchColumn(j, { description: v })}
                            onType={(v) => patchColumn(j, { type: v as ColumnType })}
                            onValues={(v) => patchColumn(j, { values: v })}
                          />
                        </div>
                        <div className="flex flex-wrap items-center gap-x-2 border-t border-ink/30 pt-2">
                          <button
                            type="button"
                            className={BTN}
                            disabled={j === 0}
                            aria-label={`Поднять колонку «${colTitle}» выше`}
                            onClick={() => patch({ columns: move(field.columns, j, -1) })}
                          >
                            ↑ Выше
                          </button>
                          <button
                            type="button"
                            className={BTN}
                            disabled={j === field.columns.length - 1}
                            aria-label={`Опустить колонку «${colTitle}» ниже`}
                            onClick={() => patch({ columns: move(field.columns, j, 1) })}
                          >
                            ↓ Ниже
                          </button>
                          <button
                            type="button"
                            className={`${BTN} text-accent-text`}
                            aria-label={`Удалить колонку «${colTitle}»`}
                            onClick={() => removeColumn(j)}
                          >
                            Удалить колонку
                          </button>
                        </div>
                      </fieldset>
                    </li>
                  );
                })}
              </ol>
            ) : (
              <p className="text-muted">Колонок пока нет.</p>
            )}
            <div>
              <button type="button" className={BTN} onClick={onAddColumn}>
                Добавить колонку
              </button>
            </div>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-x-2 border-t-2 border-ink pt-2">
          <button
            type="button"
            className={BTN}
            disabled={index === 0}
            aria-label={`Поднять поле «${title}» выше`}
            onClick={() => onMove(-1)}
          >
            ↑ Выше
          </button>
          <button
            type="button"
            className={BTN}
            disabled={index === count - 1}
            aria-label={`Опустить поле «${title}» ниже`}
            onClick={() => onMove(1)}
          >
            ↓ Ниже
          </button>
          <button
            type="button"
            className={`${BTN} text-accent-text`}
            aria-label={`Удалить поле «${title}»`}
            onClick={remove}
          >
            Удалить поле
          </button>
        </div>
      </fieldset>
    </li>
  );
}
