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
  type: ColumnType;
  description?: string;
  values?: string[] | null;
}

export interface SchemaField extends Omit<SchemaColumn, "type"> {
  type: FieldType;
  required?: boolean;
  columns?: SchemaColumn[] | null;
}

/** GET /v1/schemas/{slug}?version= */
export interface DocSchema {
  slug: string;
  version: number;
  title: string;
  description: string;
  fields: SchemaField[];
}
