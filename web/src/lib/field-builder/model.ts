// Модель редактора типа документа и перевод в DocSchema и обратно.
import type { ColumnType, DocSchema, FieldType, RuleSpec, SchemaColumn, SchemaField } from "../types";
import { DOC_RULE_PARAMS, rulePhrase, ruleRefs, rulesUsing } from "./rules.ts";
import { toIdentifier, uniqueName } from "./translit.ts";

export type CheckKind = "none" | "inn" | "kpp" | "bik" | "ogrn" | "snils" | "dictionary" | "regex";

export interface EditorColumn {
  uid: string;
  name: string;
  isNew: boolean;
  title: string;
  description: string;
  type: ColumnType;
  values: string; // варианты enum, по одному в строке
  base: SchemaColumn | null; // как пришло с сервера: для сохранения нетронутого
}

export interface EditorField extends Omit<EditorColumn, "type" | "base"> {
  type: FieldType;
  required: boolean;
  check: CheckKind;
  checkParam: string;
  notFuture: boolean;
  columns: EditorColumn[];
  base: SchemaField | null;
}

export interface EditorDoc {
  slug: string;
  isNew: boolean;
  title: string;
  description: string;
  version: number; // 0 — новый тип
  fields: EditorField[];
  rules: RuleSpec[];
  seq: number; // счётчик uid новых строк
  // имена сохранённых полей («поле») и колонок («таблица.колонка»), удалённых в этой правке: новым строкам не даём
  reserved: string[];
}

export const TYPE_OPTIONS: { value: FieldType; label: string }[] = [
  { value: "string", label: "Текст" },
  { value: "number", label: "Число" },
  { value: "integer", label: "Целое число" },
  { value: "date", label: "Дата" },
  { value: "boolean", label: "Да/нет" },
  { value: "enum", label: "Выбор из списка" },
  { value: "table", label: "Таблица" },
];

export const CHECK_OPTIONS: { value: CheckKind; label: string }[] = [
  { value: "none", label: "нет" },
  { value: "inn", label: "ИНН" },
  { value: "kpp", label: "КПП" },
  { value: "bik", label: "БИК" },
  { value: "ogrn", label: "ОГРН" },
  { value: "snils", label: "СНИЛС" },
  { value: "dictionary", label: "Справочник" },
  { value: "regex", label: "Шаблон" },
];

const CHECK_KINDS: ReadonlySet<string> = new Set(CHECK_OPTIONS.map((o) => o.value).filter((v) => v !== "none"));
const NOT_FUTURE = "date_not_future";
const SLUG_RE = /^[a-z0-9_]{2,40}$/;

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Варианты enum: строки trim, без пустых и повторов; пусто -> null. */
function parseValues(text: string): string[] | null {
  const out: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const v = line.trim();
    if (v && !out.includes(v)) out.push(v);
  }
  return out.length ? out : null;
}

function checkParamOf(rule: RuleSpec): string {
  if (rule.kind === "regex") return str(rule.params.pattern);
  if (rule.kind === "dictionary") return str(rule.params.name);
  return "";
}

function columnFromSchema(c: SchemaColumn, uid: string): EditorColumn {
  return {
    uid,
    name: c.name,
    isNew: false,
    title: c.title ?? "",
    description: c.description ?? "",
    type: c.type,
    values: (c.values ?? []).join("\n"),
    base: c,
  };
}

function fieldFromSchema(f: SchemaField, i: number): EditorField {
  const rules = f.rules ?? [];
  const check = rules.find((r) => CHECK_KINDS.has(r.kind));
  return {
    uid: `f${i}`,
    name: f.name,
    isNew: false,
    title: f.title ?? "",
    description: f.description ?? "",
    type: f.type,
    values: (f.values ?? []).join("\n"),
    required: !!f.required,
    check: check ? (check.kind as CheckKind) : "none",
    checkParam: check ? checkParamOf(check) : "",
    notFuture: rules.some((r) => r.kind === NOT_FUTURE),
    columns: (f.columns ?? []).map((c, j) => columnFromSchema(c, `f${i}c${j}`)),
    base: f,
  };
}

export function fromSchema(schema: DocSchema): EditorDoc {
  return {
    slug: schema.slug,
    isNew: false,
    title: schema.title,
    description: schema.description,
    version: schema.version,
    fields: schema.fields.map(fieldFromSchema),
    rules: schema.rules ?? [],
    seq: 0,
    reserved: [],
  };
}

function blankField(uid: string): EditorField {
  return {
    uid,
    name: "",
    isNew: true,
    title: "",
    description: "",
    type: "string",
    values: "",
    required: false,
    check: "none",
    checkParam: "",
    notFuture: false,
    columns: [],
    base: null,
  };
}

export function newDoc(): EditorDoc {
  // «Номер» и «Дата» — особые поля очереди и выгрузок, имена у них фиксированы.
  const number: EditorField = {
    ...blankField("f0"),
    name: "number",
    isNew: false,
    title: "Номер документа",
    description: "Номер документа строкой, как написан.",
    required: true,
  };
  const issued: EditorField = {
    ...blankField("f1"),
    name: "issued",
    isNew: false,
    title: "Дата документа",
    description: "Дата документа. null, если даты нет.",
    type: "date",
    required: true,
    notFuture: true,
  };
  return {
    slug: "",
    isNew: true,
    title: "",
    description: "",
    version: 0,
    fields: [number, issued],
    rules: [],
    seq: 0,
    reserved: [],
  };
}

/** Новое поле с uid `n<seq>`; в список его вставляет вызывающий. */
export function newField(doc: EditorDoc): [EditorDoc, EditorField] {
  return [{ ...doc, seq: doc.seq + 1 }, blankField(`n${doc.seq}`)];
}

/** Новая колонка с uid `n<seq>`; в таблицу её вставляет вызывающий. */
export function newColumn(doc: EditorDoc): [EditorDoc, EditorColumn] {
  const col: EditorColumn = {
    uid: `n${doc.seq}`,
    name: "",
    isNew: true,
    title: "",
    description: "",
    type: "string",
    values: "",
    base: null,
  };
  return [{ ...doc, seq: doc.seq + 1 }, col];
}

/** Имена новых строк по названиям; старые имена занимают место первыми и не меняются. */
function assignNames<T extends { name: string; isNew: boolean; title: string }>(
  rows: readonly T[],
  renamed: Map<string, string>,
  reserved: readonly string[],
): T[] {
  const taken = new Set([...reserved, ...rows.filter((r) => !r.isNew).map((r) => r.name)]);
  return rows.map((r) => {
    if (!r.isNew) return r;
    const name = uniqueName(toIdentifier(r.title), taken);
    taken.add(name);
    if (name === r.name) return r;
    if (r.name) renamed.set(r.name, name);
    return { ...r, name };
  });
}

function rewriteRule(
  rule: RuleSpec,
  fieldRenamed: Map<string, string>,
  colRenamed: Map<string, Map<string, string>>,
): RuleSpec {
  const spec = DOC_RULE_PARAMS[rule.kind];
  if (!spec) return rule;
  const cols = colRenamed.get(str(rule.params.table));
  let params: Record<string, unknown> | null = null;
  for (const [param, kind] of Object.entries(spec)) {
    const v = str(rule.params[param]);
    if (!v || kind === "int") continue;
    const next = kind === "column" ? cols?.get(v) : fieldRenamed.get(v);
    if (next === undefined || next === v) continue;
    params ??= { ...rule.params };
    params[param] = next;
  }
  return params ? { ...rule, params } : rule;
}

export function withNames(doc: EditorDoc): EditorDoc {
  const fieldRenamed = new Map<string, string>();
  // колонки — по прежнему имени таблицы: так на них ссылаются правила до переписывания
  const colRenamed = new Map<string, Map<string, string>>();
  const reservedFields = doc.reserved.filter((k) => !k.includes("."));
  const fields = assignNames(doc.fields, fieldRenamed, reservedFields).map((f, i) => {
    const renamed = new Map<string, string>();
    const prefix = `${f.name}.`;
    const reservedCols = doc.reserved.filter((k) => k.startsWith(prefix)).map((k) => k.slice(prefix.length));
    const columns = assignNames(f.columns, renamed, reservedCols);
    if (renamed.size) colRenamed.set(doc.fields[i].name, renamed);
    return columns === f.columns || columns.every((c, j) => c === f.columns[j]) ? f : { ...f, columns };
  });
  const changed = fieldRenamed.size > 0 || colRenamed.size > 0;
  const rules = changed ? doc.rules.map((r) => rewriteRule(r, fieldRenamed, colRenamed)) : doc.rules;
  return { ...doc, fields, rules };
}

function checkRule(f: EditorField, base: RuleSpec | null): RuleSpec {
  let params: Record<string, unknown> = {};
  if (f.check === "regex") params = { pattern: f.checkParam };
  else if (f.check === "dictionary") params = { name: f.checkParam };
  // тот же вид проверки: прочие параметры (fuzzy справочника) остаются
  if (base && base.kind === f.check) params = { ...base.params, ...params };
  return { kind: f.check, params };
}

/** Правила поля: порядок base.rules, первая проверка меняется на месте, «не в будущем» — по флагу. */
function fieldRules(f: EditorField): RuleSpec[] {
  const hasCheck = f.check !== "none" && f.type !== "table";
  const notFuture = f.notFuture && f.type === "date";
  const out: RuleSpec[] = [];
  let checkSeen = false;
  let checkPlaced = false;
  let notFuturePlaced = false;
  for (const r of f.base?.rules ?? []) {
    if (r.kind === NOT_FUTURE) {
      if (notFuture) {
        out.push(r);
        notFuturePlaced = true;
      }
    } else if (CHECK_KINDS.has(r.kind) && !checkSeen) {
      checkSeen = true;
      if (hasCheck) {
        out.push(checkRule(f, r));
        checkPlaced = true;
      }
    } else {
      out.push(r);
    }
  }
  if (hasCheck && !checkPlaced) out.push(checkRule(f, null));
  if (notFuture && !notFuturePlaced) out.push({ kind: NOT_FUTURE, params: {} });
  return out;
}

function columnToSchema(c: EditorColumn): SchemaColumn {
  return {
    name: c.name,
    type: c.type,
    title: c.title.trim() || null,
    description: c.description,
    values: c.type === "enum" ? parseValues(c.values) : null,
  };
}

function fieldToSchema(f: EditorField): SchemaField {
  const title = f.title.trim();
  // модель не видит title: новому полю без подсказки нужно хотя бы человеческое имя
  const description = f.isNew && !f.description.trim() ? title : f.description;
  return {
    name: f.name,
    type: f.type,
    title: title || null,
    description,
    values: f.type === "enum" ? parseValues(f.values) : null,
    required: f.required,
    columns: f.type === "table" ? f.columns.map(columnToSchema) : null,
    rules: fieldRules(f),
  };
}

export function toSchema(doc: EditorDoc): DocSchema {
  const d = withNames(doc);
  return {
    slug: d.slug,
    version: d.version,
    title: d.title.trim(),
    description: d.description,
    fields: d.fields.map(fieldToSchema),
    rules: d.rules,
  };
}

/** Есть ли в схеме то, на что ссылается правило: поле, таблица (именно таблица) или колонка «таблица.колонка». */
function missingRef(rule: RuleSpec, schema: DocSchema): string | null {
  const byName = new Map(schema.fields.map((f) => [f.name, f]));
  const table = str(rule.params.table);
  for (const ref of ruleRefs(rule)) {
    const dot = ref.indexOf(".");
    if (dot >= 0) {
      const t = byName.get(ref.slice(0, dot));
      const col = ref.slice(dot + 1);
      if (t?.type !== "table" || !(t.columns ?? []).some((c) => c.name === col)) return ref;
    } else {
      const f = byName.get(ref);
      if (!f || (ref === table && f.type !== "table")) return ref;
    }
  }
  return null;
}

export function validateDoc(doc: EditorDoc, takenSlugs: readonly string[]): Record<string, string> {
  const errs: Record<string, string> = {};
  if (!doc.title.trim()) errs.title = "Введите название типа";
  if (doc.isNew) {
    if (!SLUG_RE.test(doc.slug)) errs.slug = "Служебное имя: 2–40 символов, латинские строчные буквы, цифры и «_»";
    else if (takenSlugs.includes(doc.slug)) errs.slug = "Тип с таким служебным именем уже есть";
  }
  if (!doc.fields.length) errs.fields = "Добавьте хотя бы одно поле";
  let tableSeen = false;
  doc.fields.forEach((f, i) => {
    const key = `fields.${i}`;
    if (f.isNew && !f.title.trim()) errs[`${key}.title`] = "Введите название поля";
    if (f.type === "enum" && !parseValues(f.values)) errs[`${key}.values`] = "Добавьте хотя бы один вариант";
    if (f.type === "table") {
      if (tableSeen) errs[`${key}.type`] = "Таблица в типе может быть только одна";
      tableSeen = true;
      if (!f.columns.length) errs[`${key}.columns`] = "Добавьте хотя бы одну колонку";
      f.columns.forEach((c, j) => {
        const ck = `${key}.columns.${j}`;
        if (c.isNew && !c.title.trim()) errs[`${ck}.title`] = "Введите название колонки";
        if (c.type === "enum" && !parseValues(c.values)) errs[`${ck}.values`] = "Добавьте хотя бы один вариант";
      });
    } else if (f.check === "dictionary" && !f.checkParam.trim()) {
      errs[`${key}.check`] = "Выберите справочник";
    } else if (f.check === "regex" && !f.checkParam.trim()) {
      errs[`${key}.check`] = "Введите шаблон";
    }
  });
  const schema = toSchema(doc);
  (schema.rules ?? []).forEach((r, j) => {
    const ref = missingRef(r, schema);
    if (ref) errs[`rules.${j}`] = `Правило ссылается на то, чего уже нет: «${ref}»`;
  });
  return errs;
}

/** Подпись для фраз правил: подпись компании -> title -> name. Ключ колонки — «таблица.колонка». */
export function titleResolver(doc: EditorDoc, labels: Record<string, string>): (key: string) => string {
  const d = withNames(doc);
  return (key: string): string => {
    if (Object.hasOwn(labels, key) && labels[key]) return labels[key];
    const dot = key.indexOf(".");
    const fname = dot >= 0 ? key.slice(0, dot) : key;
    const f = d.fields.find((x) => x.name === fname);
    if (dot < 0) return f?.title.trim() || key;
    const cname = key.slice(dot + 1);
    const c = f?.columns.find((x) => x.name === cname);
    return c?.title.trim() || cname;
  };
}

/** Фразы правил документа, которые мешают удалить поле или колонку. */
export function deleteBlockers(doc: EditorDoc, fieldIndex: number, columnIndex?: number): string[] {
  const d = withNames(doc);
  const f = d.fields[fieldIndex];
  if (!f) return [];
  let key = f.name;
  if (columnIndex !== undefined) {
    const c = f.columns[columnIndex];
    if (!c) return [];
    key = `${f.name}.${c.name}`;
  }
  const titleOf = titleResolver(d, {});
  return rulesUsing(d.rules, key).map((i) => rulePhrase(d.rules[i], titleOf));
}

/** Удалить поле; имя сохранённого поля остаётся занятым до конца правки. */
export function removeField(doc: EditorDoc, index: number): EditorDoc {
  const f = doc.fields[index];
  if (!f) return doc;
  return {
    ...doc,
    fields: doc.fields.filter((_, i) => i !== index),
    reserved: f.base ? [...doc.reserved, f.name] : doc.reserved,
  };
}

/** Удалить колонку таблицы; имя сохранённой колонки остаётся занятым в этой таблице. */
export function removeColumn(doc: EditorDoc, fieldIndex: number, columnIndex: number): EditorDoc {
  const f = doc.fields[fieldIndex];
  const c = f?.columns[columnIndex];
  if (!c) return doc;
  return {
    ...doc,
    fields: doc.fields.map((x, i) =>
      i === fieldIndex ? { ...x, columns: x.columns.filter((_, j) => j !== columnIndex) } : x,
    ),
    reserved: c.base ? [...doc.reserved, `${f.name}.${c.name}`] : doc.reserved,
  };
}

/** Сдвиг элемента на одну позицию; на краю списка — копия без изменений. */
export function move<T>(list: readonly T[], index: number, delta: -1 | 1): T[] {
  const out = list.slice();
  const to = index + delta;
  if (index < 0 || index >= out.length || to < 0 || to >= out.length) return out;
  [out[index], out[to]] = [out[to], out[index]];
  return out;
}
