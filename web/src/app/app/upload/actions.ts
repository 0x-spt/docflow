"use server";

import { redirect, unstable_rethrow } from "next/navigation";

import { ApiError } from "@/lib/api";
import { apiFetchAuthed } from "@/lib/session";

export type UploadState = { error?: string };

const MODES = new Set(["gray", "color", "bw"]);
const FAILED = "Не удалось загрузить документ: сервис недоступен. Попробуйте ещё раз.";

/**
 * Загрузка документа: пересылаем файлы в API как multipart. Ключ идемпотентности приходит из формы
 * (один на показ страницы), поэтому двойной клик или повтор не создадут второй документ.
 */
export async function uploadDocument(_prev: UploadState, form: FormData): Promise<UploadState> {
  const schema = String(form.get("schema") ?? "").trim();
  const mode = String(form.get("mode") ?? "gray");
  const key = String(form.get("idempotency_key") ?? "").trim();
  // пустое поле выбора файла браузер присылает как File без имени и размера
  const files = form.getAll("files").filter((f): f is File => f instanceof File && f.size > 0);

  if (!schema) return { error: "Выберите тип документа" };
  if (!MODES.has(mode)) return { error: "Выберите режим скана" };
  if (files.length === 0) return { error: "Выберите хотя бы один файл" };

  const body = new FormData();
  body.set("schema", schema);
  body.set("mode", mode);
  for (const f of files) body.append("files", f, f.name);

  let id: string;
  try {
    const accepted = await apiFetchAuthed<{ id: string; status: string }>("/v1/documents", {
      method: "POST",
      headers: key ? { "Idempotency-Key": key.slice(0, 255) } : undefined,
      body,
    });
    id = accepted.id;
  } catch (e) {
    unstable_rethrow(e); // редирект на /login при 401
    if (e instanceof ApiError && e.status >= 400 && e.status < 500) return { error: e.detail };
    console.error("документ не загружен", e);
    return { error: FAILED };
  }
  redirect(`/app/documents/${id}`);
}
