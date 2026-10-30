"use client";

import { startTransition, useActionState, useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";

import { uploadDocument, type UploadState } from "@/app/app/upload/actions";
import { Button, Field } from "@/components/ui";
import { AUTO_SLUG } from "@/lib/doc-type";
import type { SchemaListItem } from "@/lib/types";

// лимиты API (по умолчанию): 20 МБ на файл, 30 файлов/страниц на документ
const MAX_FILE_MB = 20;
const MAX_FILES = 30;
const INITIAL: UploadState = {};

const MODES = [
  { value: "gray", label: "Серый", hint: "для большинства сканов и фото" },
  { value: "color", label: "Цветной", hint: "печати, цветные отметки" },
  { value: "bw", label: "Ч-Б", hint: "контрастные чёрно-белые копии" },
];

function size(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

/** Форма загрузки: схема, файлы (input + перетаскивание), режим. Без JS работает как обычная форма. */
export function UploadForm({ schemas }: { schemas: SchemaListItem[] }) {
  const [state, action, pending] = useActionState(uploadDocument, INITIAL);
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const inputId = useId();

  useEffect(() => {
    if (state.error) alertRef.current?.focus();
  }, [state]);

  function sync() {
    setFiles(Array.from(input.current?.files ?? []));
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
    if (!input.current || !e.dataTransfer.files.length) return;
    input.current.files = e.dataTransfer.files; // в input, чтобы форма отправила их как обычный выбор
    sync();
  }

  // отправка вручную: автосброс формы React 19 стёр бы выбранные файлы при ошибке
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = new FormData(event.currentTarget);
    startTransition(() => action(form));
  }

  const big = files.filter((f) => f.size > MAX_FILE_MB * 1024 * 1024);
  const tooMany = files.length > MAX_FILES;
  const total = files.reduce((n, f) => n + f.size, 0);

  return (
    <form action={action} onSubmit={onSubmit} aria-busy={pending} className="flex flex-col gap-6">
      {state.error ? (
        <div
          ref={alertRef}
          tabIndex={-1}
          role="alert"
          className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text"
        >
          {state.error}
        </div>
      ) : null}

      <Field as="select" label="Тип документа" name="schema" required defaultValue={AUTO_SLUG}>
        <option value={AUTO_SLUG}>Определить автоматически</option>
        {schemas.map((s) => (
          <option key={s.slug} value={s.slug}>
            {s.title}
          </option>
        ))}
      </Field>

      <div className="flex flex-col gap-1.5">
        <label htmlFor={inputId} className="font-mono text-sm font-medium">
          Файлы документа
        </label>
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          className={`flex min-h-28 flex-col items-center justify-center gap-1 border-2 border-dashed border-ink p-5 text-center ${
            over ? "bg-paper" : "bg-card"
          }`}
        >
          <span className="font-medium">Перетащите файлы сюда или выберите ниже</span>
          <span className="text-sm text-muted">
            JPEG, PNG, WEBP, TIFF, HEIC, PDF · до {MAX_FILE_MB} МБ на файл · до {MAX_FILES} файлов. Несколько файлов
            — страницы одного документа по порядку.
          </span>
        </div>
        <input
          ref={input}
          id={inputId}
          type="file"
          name="files"
          multiple
          required
          accept="image/jpeg,image/png,image/webp,image/tiff,image/heic,image/heif,application/pdf,.heic,.heif"
          onChange={sync}
          className="min-h-11 w-full border-2 border-ink bg-card px-3 py-2 text-base"
        />
      </div>

      {files.length ? (
        <div className="border-2 border-ink bg-card">
          <p className="border-b-2 border-ink px-3 py-2 font-mono text-sm">
            Выбрано: {files.length} · {size(total)}
          </p>
          <ul className="divide-y divide-ink/30 text-sm">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="flex justify-between gap-4 px-3 py-1.5">
                <span className="min-w-0 truncate">{f.name}</span>
                <span
                  className={`shrink-0 font-mono tabular-nums ${
                    f.size > MAX_FILE_MB * 1024 * 1024 ? "font-medium text-accent-text" : ""
                  }`}
                >
                  {size(f.size)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {big.length || tooMany ? (
        <div role="status" className="border-2 border-accent-text bg-paper p-4 text-accent-text">
          {big.length ? (
            <p className="font-medium">
              Файл больше {MAX_FILE_MB} МБ: {big.map((f) => f.name).join(", ")}. Сервис его не примет — уменьшите или
              пересохраните.
            </p>
          ) : null}
          {tooMany ? <p className="font-medium">Файлов больше {MAX_FILES}: сервис не примет такой документ.</p> : null}
        </div>
      ) : null}

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-mono text-sm font-medium">Режим скана</legend>
        <div className="flex flex-wrap border-2 border-ink bg-card">
          {MODES.map((m, i) => (
            <label
              key={m.value}
              className="flex min-h-11 flex-1 basis-40 cursor-pointer items-center gap-3 border-ink px-4 py-2 not-last:border-r-2 has-[:checked]:bg-ink has-[:checked]:text-paper"
            >
              <input type="radio" name="mode" value={m.value} defaultChecked={i === 0} className="size-4 accent-accent" />
              <span>
                <span className="block font-medium">{m.label}</span>
                <span className="block text-xs opacity-80">{m.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div>
        <Button type="submit" variant="primary" disabled={pending}>
          {pending ? "Загружаем…" : "Загрузить"}
        </Button>
      </div>
    </form>
  );
}
