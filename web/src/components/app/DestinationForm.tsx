"use client";

import { useActionState, useMemo, useState } from "react";

import { saveDestination, type SaveState } from "@/app/app/integrations/actions";
import { Button, Field } from "@/components/ui";
import type { ConnectorInfo, Destination, FieldDef, MappingSchema } from "@/lib/types";

const INITIAL: SaveState = {};

const LINK_BTN =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text";

/** Что вводится в правой колонке сопоставления у систем без готового списка полей. */
const FREE_TARGET: Record<string, { label: string; placeholder: string; help: string }> = {
  google_sheets: {
    label: "Название колонки",
    placeholder: "как в шапке таблицы",
    help: "В таблицу попадут только перечисленные поля, в этом порядке. Если не указать ни одного, выгружаются все поля схемы.",
  },
  bitrix24: {
    label: "Код поля Битрикс24",
    placeholder: "например UF_CRM_INN",
    help: "Какое поле карточки заполнить значением из документа. Не указанные поля не выгружаются.",
  },
  amocrm: {
    label: "Поле amoCRM",
    placeholder: "числовой ID или код поля",
    help: "Какое поле карточки заполнить значением из документа. Не указанные поля не выгружаются.",
  },
};

export function hasMapping(c: ConnectorInfo): boolean {
  return c.target_fields.length > 0 || Object.hasOwn(FREE_TARGET, c.kind);
}

function ConfigInput({ f, value }: { f: FieldDef; value: string }) {
  const common = {
    label: f.label,
    name: `c_${f.name}`,
    hint: f.help || undefined,
    required: f.required,
    defaultValue: value,
  };
  if (f.type === "textarea") return <Field {...common} as="textarea" rows={4} autoComplete="off" />;
  if (f.type === "select")
    return (
      <Field {...common} as="select">
        {f.required ? null : <option value="">Не выбрано</option>}
        {(f.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Field>
    );
  return (
    <Field
      {...common}
      type={f.type === "url" ? "url" : "text"}
      inputMode={f.type === "number" ? "decimal" : undefined}
      autoComplete="off"
      className={f.type === "url" ? "font-mono" : ""}
    />
  );
}

function SecretInput({ f, saved }: { f: FieldDef; saved: boolean }) {
  const name = `s_${f.name}`;
  const [replacing, setReplacing] = useState(false);
  if (f.type === "textarea") {
    // сохранённый секрет не показываем и не подставляем: чтобы заменить, нужно явно нажать «Заменить»
    if (saved && !replacing) {
      return (
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-sm font-medium">{f.label}</span>
          <div className="flex flex-wrap items-center gap-3 border-2 border-ink bg-paper px-3 py-1">
            <span className="flex-1 font-mono tracking-widest" aria-hidden="true">
              ••••••••••••
            </span>
            <span className="sr-only">Значение сохранено и скрыто.</span>
            <button type="button" onClick={() => setReplacing(true)} className={LINK_BTN}>
              Заменить<span className="sr-only">: {f.label}</span>
            </button>
          </div>
          {f.help ? <p className="text-sm text-muted">{f.help}</p> : null}
        </div>
      );
    }
    return (
      <div className="flex flex-col gap-2">
        <Field
          label={f.label}
          name={name}
          as="textarea"
          rows={5}
          autoComplete="off"
          spellCheck={false}
          hint={f.help || undefined}
          required={f.required && !saved}
          className="font-mono [-webkit-text-security:disc]"
        />
        {saved ? (
          <div>
            <button type="button" onClick={() => setReplacing(false)} className={LINK_BTN}>
              Отмена: оставить сохранённое
            </button>
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <Field
      label={f.label}
      name={name}
      type="password"
      autoComplete="new-password"
      required={f.required && !saved}
      placeholder={saved ? "••••••••" : undefined}
      hint={[f.help, saved ? "Сохранено и не показывается." : ""].filter(Boolean).join(" ") || undefined}
      className="font-mono"
    />
  );
}

function MappingEditor({
  connector,
  schemas,
  value,
  onChange,
}: {
  connector: ConnectorInfo;
  schemas: MappingSchema[];
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const free = FREE_TARGET[connector.kind];
  const withTables = connector.kind === "onec_odata";
  const [slug, setSlug] = useState(() => {
    // схема, под которую уже сохранено больше всего полей
    const keys = new Set(Object.keys(value));
    let best = schemas[0]?.slug ?? "";
    let score = -1;
    for (const s of schemas) {
      const n = s.rows.filter((r) => keys.has(r.key)).length;
      if (n > score) {
        best = s.slug;
        score = n;
      }
    }
    return best;
  });
  const schema = schemas.find((s) => s.slug === slug);
  const rows = useMemo(() => (schema?.rows ?? []).filter((r) => withTables || !r.group), [schema, withTables]);

  if (schemas.length === 0) {
    return <p className="border-2 border-ink bg-card p-4">Схем документов пока нет: сопоставлять нечего.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-3xl text-muted">
        {free?.help ??
          "Какое поле документа в какое поле системы попадёт. Не указанные поля не выгружаются или берутся по умолчанию."}
      </p>
      <div className="max-w-md">
        <Field
          label="Схема документа"
          name="mapping_schema"
          as="select"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
        >
          {schemas.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.title}
            </option>
          ))}
        </Field>
      </div>
      <ul className="flex flex-col border-2 border-ink bg-card">
        {rows.map((r, i) => {
          const id = `map-${i}`;
          const current = value[r.key] ?? "";
          return (
            <li
              key={r.key}
              className="grid gap-2 border-t-2 border-ink p-3 first:border-t-0 sm:grid-cols-2 sm:items-center sm:gap-4"
            >
              <label htmlFor={id} className="min-w-0">
                <span className="block font-mono text-xs text-muted">Поле документа</span>
                <span className="block break-words font-medium">{r.label}</span>
                <span className="sr-only"> — {free ? free.label : "поле системы"}</span>
              </label>
              {free ? (
                <input
                  id={id}
                  type="text"
                  value={current}
                  maxLength={200}
                  autoComplete="off"
                  placeholder={free.placeholder}
                  onChange={(e) => onChange({ ...value, [r.key]: e.target.value })}
                  className="min-h-11 w-full border-2 border-ink bg-paper px-3 py-2 text-base"
                />
              ) : (
                <select
                  id={id}
                  value={current}
                  onChange={(e) => onChange({ ...value, [r.key]: e.target.value })}
                  className="min-h-11 w-full border-2 border-ink bg-paper px-3 py-2 text-base"
                >
                  <option value="">не выгружать</option>
                  {connector.target_fields.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.label}
                    </option>
                  ))}
                </select>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Форма подключения, собранная по каталогу: поля, секреты (пустые = не менять) и сопоставление полей. */
export function DestinationForm({
  connector,
  destination,
  schemas,
}: {
  connector: ConnectorInfo;
  destination?: Destination;
  schemas: MappingSchema[];
}) {
  const [state, action, pending] = useActionState(saveDestination, INITIAL);
  const [mapping, setMapping] = useState<Record<string, string>>(destination?.mapping ?? {});
  const editing = Boolean(destination);
  const savedSecrets = Boolean(destination?.has_secrets);

  const cfg = (f: FieldDef): string => {
    const fromState = state.config?.[f.name];
    if (fromState !== undefined) return fromState;
    const v = destination?.config[f.name];
    return v === undefined || v === null ? "" : String(v);
  };

  // пустые значения не отправляем
  const mappingJson = JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v.trim() !== "")));

  return (
    <form action={action} aria-busy={pending} className="flex flex-col gap-8">
      <input type="hidden" name="kind" value={connector.kind} />
      {destination ? <input type="hidden" name="id" value={destination.id} /> : null}
      <input type="hidden" name="mapping" value={hasMapping(connector) ? mappingJson : "{}"} />

      <div role="alert">
        {state.error ? (
          <p className="break-words border-2 border-accent-text bg-paper p-4 font-medium text-accent-text">
            {state.error}
          </p>
        ) : null}
      </div>
      {state.saved ? (
        <p role="status" className="border-2 border-ink bg-card p-4 font-medium">
          Сохранено.
        </p>
      ) : null}

      <section aria-labelledby="dest-main" className="flex max-w-2xl flex-col gap-4">
        <h2 id="dest-main" className="text-2xl font-semibold tracking-tight">
          Настройки
        </h2>
        <Field
          label="Название"
          name="name"
          required
          maxLength={200}
          autoComplete="off"
          defaultValue={state.name ?? destination?.name ?? connector.label}
          hint="Для вас: чтобы отличать подключения друг от друга."
        />
        {connector.config_fields.map((f) => (
          <ConfigInput key={f.name} f={f} value={cfg(f)} />
        ))}
        {connector.secret_fields.length > 0 ? (
          <fieldset className="flex flex-col gap-4 border-2 border-ink bg-card p-4">
            <legend className="px-2 font-mono text-sm font-medium">Секреты</legend>
            <p className="text-sm text-muted">
              Хранятся в зашифрованном виде и больше нигде не показываются.
              {editing && savedSecrets ? " Оставьте поле пустым, чтобы не менять сохранённое." : ""}
            </p>
            {connector.secret_fields.map((f) => (
              <SecretInput key={f.name} f={f} saved={savedSecrets} />
            ))}
          </fieldset>
        ) : null}
      </section>

      {hasMapping(connector) ? (
        <section aria-labelledby="dest-map" className="flex flex-col gap-4">
          <h2 id="dest-map" className="text-2xl font-semibold tracking-tight">
            Сопоставление полей
          </h2>
          <MappingEditor connector={connector} schemas={schemas} value={mapping} onChange={setMapping} />
        </section>
      ) : null}

      <div className="flex flex-wrap gap-3">
        <Button type="submit" disabled={pending}>
          {pending ? "Сохраняем…" : editing ? "Сохранить" : "Подключить"}
        </Button>
        <Button href="/app/integrations" variant="ghost">
          {editing ? "К списку" : "Отмена"}
        </Button>
      </div>
    </form>
  );
}
