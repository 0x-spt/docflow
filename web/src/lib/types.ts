// Типы ответов API DocFlow. Источник истины — роутеры бэкенда и src/docflow/serializers.py.

export type DocStatus = "queued" | "processing" | "ok" | "needs_review" | "failed";
export type Role = "owner" | "operator";

export interface SessionUser {
  id: string;
  email: string;
  role: Role;
}

/** POST /v1/auth/login */
export interface LoginResult {
  token: string;
  expires_at: string;
  user: SessionUser;
}

/** GET /v1/me; `user` = null, когда вызвано ключом API */
export interface Me {
  user: SessionUser | null;
  tenant: { id: string; name: string; extractor: string; auto_ok: boolean };
}

export interface SummaryItem {
  field: string;
  /** Название поля для людей (своё у компании или наше). */
  label: string;
  value: string | number;
}

/** Элемент GET /v1/documents */
export interface DocumentListItem {
  id: string;
  status: DocStatus;
  schema: { slug: string; version: number };
  title: string;
  summary: SummaryItem[];
  issue: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface DocumentList {
  items: DocumentListItem[];
  next_before: string | null;
}

export interface Issue {
  field: string | null;
  code: string | null;
  message: string | null;
}

/** GET /v1/documents/{id}, POST /v1/documents/{id}/review, тело вебхука */
export interface DocumentPayload {
  id: string;
  status: DocStatus;
  schema: { slug: string; version: number };
  data: Record<string, unknown> | null;
  issues: Issue[];
  error: string | null;
  pages: number | null;
  model: string | null;
  created_at: string | null;
  finished_at: string | null;
  review: { at: string | null; note: string | null } | null;
  pdf_url: string | null;
  digital_pdf_url: string | null;
  exports?: ExportItem[];
}

/** skipped — доставка больше не нужна (уведомление устарело): не ошибка, «Повторить» нет */
export type ExportStatus = "pending" | "done" | "failed" | "skipped";

/** Выгрузка или уведомление по документу (поле `exports` в документе) */
export interface ExportItem {
  id: string;
  destination_id: string | null;
  destination_name: string | null;
  kind: string | null;
  purpose: "export" | "notify_review" | "notify_new";
  status: ExportStatus;
  external_id: string | null;
  error: string | null;
  updated_at: string | null;
}

/** GET /v1/exports: строка журнала (выгрузка + документ, номер попытки, время создания) */
export interface ExportLogItem extends ExportItem {
  document_id: string;
  attempt: number;
  next_attempt_at: string | null;
  created_at: string | null;
}

/** GET /v1/exports/summary: ошибки и очередь по получателям; failed — все ошибки тенанта */
export interface ExportsSummary {
  items: { destination_id: string; failed: number; pending: number }[];
  failed: number;
}

export type RuleOp =
  | "eq"
  | "ne"
  | "gt"
  | "ge"
  | "lt"
  | "le"
  | "contains"
  | "in_dictionary"
  | "empty"
  | "not_empty";

export interface RuleCondition {
  field: string;
  op: RuleOp;
  value?: string | number | null;
}

/** GET/PUT /v1/routing-rules */
export interface RoutingRule {
  name: string;
  schema: string | null;
  conditions: RuleCondition[];
  export_to: string[];
  require_approval: boolean;
  notify: string[];
}

export interface RoutingRules {
  rules: RoutingRule[];
  updated_at: string | null;
}

/** POST /v1/documents/{id}/review; `data` — ПОЛНЫЙ объект, не дифф */
export interface ReviewRequest {
  decision: "approve" | "reject";
  data?: Record<string, unknown>;
  note?: string;
}

/** GET /v1/stats */
export interface Stats {
  days: number;
  total: number;
  by_status: Record<DocStatus, number>;
  auto_share: number | null;
  reviewed: number;
}

export interface DictionaryEntry {
  value: string;
  aliases: string[];
}

/** GET /v1/dictionaries (без entries) и GET/PUT /v1/dictionaries/{name} */
export interface Dictionary {
  name: string;
  title: string;
  size: number;
  updated_at: string | null;
  entries?: DictionaryEntry[];
}

/** GET /v1/dictionaries/{name}/suggestions */
export interface Suggestion {
  value: string;
  code: "not_in_dictionary" | "corrected";
  count: number;
  nearest: string | null;
}

/** GET /v1/api-keys */
export interface ApiKey {
  id: string;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
}

/** POST /v1/api-keys; `key` показывается один раз */
export interface ApiKeyCreated {
  id: string;
  prefix: string;
  key: string;
  created_at: string;
}

export type WebhookEvent = "document.completed" | "document.failed" | "document.reviewed";

/** GET /v1/webhooks; POST добавляет `secret` (показывается один раз) */
export interface Webhook {
  id: string;
  url: string;
  events: WebhookEvent[];
  active: boolean;
  created_at: string | null;
}

export interface WebhookCreated extends Webhook {
  secret: string;
}

/** GET /v1/schemas */
export interface SchemaListItem {
  slug: string;
  version: number;
  title: string;
  builtin: boolean;
}

/** POST /v1/leads */
export type LeadVolume = "lt100" | "100-1000" | "1000-10000" | "gt10000" | "unknown";

export type LeadTarget = "1c" | "bitrix24" | "amocrm" | "google_sheets" | "excel" | "own_system" | "unknown";

export interface LeadRequest {
  name: string;
  company?: string;
  contact: string;
  niche?: string;
  volume: LeadVolume;
  documents?: string;
  message?: string;
  targets?: LeadTarget[];
  consent: boolean;
  website?: string;
}

export type ColumnType = "string" | "integer" | "number" | "date" | "boolean" | "enum";
export type FieldType = ColumnType | "table";

export interface SchemaColumn {
  name: string;
  title?: string | null;
  type: ColumnType;
  description?: string;
  values?: string[] | null;
}

/** Правило проверки: у поля или у документа */
export interface RuleSpec {
  kind: string;
  params: Record<string, unknown>;
}

export interface SchemaField extends Omit<SchemaColumn, "type"> {
  type: FieldType;
  required?: boolean;
  columns?: SchemaColumn[] | null;
  rules?: RuleSpec[];
}

/** Элемент detail 422: путь до места ошибки и текст */
export interface ValidationItem {
  loc: (string | number)[];
  msg: string;
}

/** GET /v1/schemas/{slug}?version= */
export interface DocSchema {
  slug: string;
  version: number;
  title: string;
  description: string;
  fields: SchemaField[];
  rules?: RuleSpec[];
}

/** Поле формы подключения (GET /v1/connectors) */
export interface FieldDef {
  name: string;
  label: string;
  required: boolean;
  help: string;
  type: "text" | "url" | "number" | "textarea" | "select";
  options?: string[] | null;
  /** подписи вариантов select: значение → подпись */
  option_labels?: Record<string, string> | null;
  /** редко нужное поле: в форме — под «Дополнительно» */
  advanced?: boolean;
}

export interface ConnectorInfo {
  kind: string;
  label: string;
  description?: string;
  can_export: boolean;
  can_notify: boolean;
  config_fields: FieldDef[];
  secret_fields: FieldDef[];
  target_fields: FieldDef[];
}

/** GET /v1/destinations: секреты не возвращаются, только признак has_secrets */
export interface Destination {
  id: string;
  kind: string;
  name: string;
  config: Record<string, unknown>;
  mapping: Record<string, string>;
  has_secrets: boolean;
  active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface TestResult {
  ok: boolean;
  message: string;
}

/** Поле системы получателя (колонка таблицы, пользовательское поле CRM, роль 1С). */
export interface RemoteField {
  code: string;
  title: string;
}

/** Ответ «Подтянуть поля»: список полей системы и подбор «ключ поля документа -> code». */
export type RemoteFieldsResult =
  | { ok: true; fields: RemoteField[]; suggested: Record<string, string> }
  | { ok: false; message: string };

/** Схема для сопоставления полей: строки «поле документа» (ключ маппинга) */
export interface MappingSchema {
  slug: string;
  title: string;
  rows: { key: string; label: string; group?: string }[];
}

/** Строка GET/PUT /v1/schemas/{slug}/labels */
export interface FieldLabelRow {
  key: string;
  type: string;
  /** Для колонок таблицы — имя таблицы, иначе null. */
  group: string | null;
  default: string;
  label: string;
  queue: boolean;
}

export interface FieldLabels {
  slug: string;
  title: string;
  version: number;
  rows: FieldLabelRow[];
  queue_fields: string[];
  queue_default: boolean;
}
