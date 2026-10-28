"use server";

import { ApiError, apiFetch } from "@/lib/api";
import { TARGETS, VOLUMES, type LeadState } from "@/lib/leads";
import type { LeadRequest, LeadTarget, LeadVolume } from "@/lib/types";

const TOO_MANY = "Слишком много заявок с вашего адреса, попробуйте позже.";
const FAILED = "Не получилось отправить заявку. Попробуйте ещё раз или напишите нам на [EMAIL].";

const TEXT_FIELDS = ["name", "company", "contact", "niche", "volume", "documents", "message"] as const;

function str(form: FormData, key: string): string {
  const v = form.get(key);
  return typeof v === "string" ? v.trim() : "";
}

// известные русские ответы API (src/docflow/api/leads.py); прочий текст 422 (pydantic, по-английски) не показываем
const KNOWN_422: Record<string, string> = {
  "нужно согласие на обработку персональных данных":
    "Без согласия на обработку персональных данных мы не можем принять заявку.",
};

function humanize(detail: string): string {
  for (const [needle, text] of Object.entries(KNOWN_422)) if (detail.includes(needle)) return text;
  return FAILED;
}

// отмеченные системы: только известные коды, без повторов, в порядке списка
function targets(form: FormData): LeadTarget[] {
  const picked = new Set(form.getAll("targets").filter((v): v is string => typeof v === "string"));
  return TARGETS.map((t) => t.value).filter((v) => picked.has(v));
}

/** Заявка на аудит: проверка полей, затем POST /v1/leads без сессии. */
export async function submitLead(_prev: LeadState, form: FormData): Promise<LeadState> {
  const values: Record<string, string> = {};
  for (const key of TEXT_FIELDS) values[key] = str(form, key);
  const consent = form.get("consent") === "on";
  if (consent) values.consent = "on";
  const picked = targets(form);
  if (picked.length) values.targets = picked.join(",");

  const errors: Record<string, string> = {};
  if (!values.name) errors.name = "Как к вам обращаться?";
  if (values.contact.length < 3) errors.contact = "Оставьте телефон, Telegram или email — иначе мы не сможем ответить.";
  if (!VOLUMES.some((v) => v.value === values.volume)) errors.volume = "Выберите объём — можно «пока не знаю».";
  if (!consent) errors.consent = "Без согласия на обработку персональных данных мы не можем принять заявку.";
  if (Object.keys(errors).length) return { ok: false, errors, values };

  const body: LeadRequest = {
    name: values.name,
    company: values.company || undefined,
    contact: values.contact,
    niche: values.niche || undefined,
    volume: values.volume as LeadVolume,
    documents: values.documents || undefined,
    message: values.message || undefined,
    targets: picked.length ? picked : undefined,
    consent: true,
    website: str(form, "hp_field_x") || undefined,
  };

  try {
    await apiFetch<{ id: string }>("/v1/leads", {
      method: "POST",
      auth: false,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if (e instanceof ApiError && e.status === 429) return { ok: false, message: TOO_MANY, values };
    if (e instanceof ApiError && e.status === 422) return { ok: false, message: humanize(e.detail), values };
    console.error("заявка на аудит не отправлена", e);
    return { ok: false, message: FAILED, values };
  }
  return { ok: true };
}
