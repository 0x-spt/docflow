"use client";

import { useActionState, useEffect, useMemo, useRef, useState, useTransition } from "react";

import { fetchRemoteFields, saveDestination, type SaveState } from "@/app/app/integrations/actions";
import { Button, Field } from "@/components/ui";
import type { ConnectorInfo, Destination, FieldDef, MappingSchema, RemoteField } from "@/lib/types";

const INITIAL: SaveState = {};

/** Подобранные автоматически значения: показываем как предложение, пока их не изменили или не сохранили. */
type Applied = { slug: string; values: Record<string, string>; saveState: SaveState };

const DATALIST_ID = "remote-fields-list";

const LINK_BTN =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text";

/** Что вводится в правой колонке сопоставления у систем без готового списка полей. */
const FREE_TARGET: Record<string, { label: string; placeholder: string; help: string }> = {
  google_sheets: {
    label: "Название колонки",
    placeholder: "как в шапке таблицы",
    help: "Отметьте поля, которые попадут в таблицу; колонки идут в порядке полей документа. Название колонки можно не задавать: возьмётся название поля. Если не отметить ни одного, выгружаются все поля схемы.",
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

function ConfigInput({ f, value, error }: { f: FieldDef; value: string; error?: string }) {
  const common = {
    error,
    label: f.label,
    name: `c_${f.name}`,
    hint: f.help || undefined,
    required: f.required,
    defaultValue: value,
  };
  if (f.type === "textarea") return <Field {...common} as="textarea" rows={4} autoComplete="off" />;
  if (f.type === "select") {
    const options = f.options ?? [];
    // сохранённого значения нет среди вариантов: не подставляем первый молча, а просим выбрать
    const known = options.includes(value);
    return (
      <Field {...common} defaultValue={known ? value : ""} as="select">
        {f.required && known ? null : <option value="">{f.required ? "— выберите —" : "Не выбрано"}</option>}
        {options.map((o) => (
          <option key={o} value={o}>
            {f.option_labels?.[o] ?? o}
          </option>
        ))}
      </Field>
    );
  }
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
  const replaceBtn = useRef<HTMLButtonElement>(null);
  const first = useRef(true);
  useEffect(() => {
    // фокус не теряется при смене кнопки на поле и обратно (не трогаем при первом показе)
    if (first.current) {
      first.current = false;
      return;
    }
    if (replacing) document.getElementById(`f-${name}`)?.focus();
    else replaceBtn.current?.focus();
  }, [replacing, name]);
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
            <button ref={replaceBtn} type="button" onClick={() => setReplacing(true)} className={LINK_BTN}>
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

/** Схема, под которую уже сохранено больше всего полей. */
function initialSchema(schemas: MappingSchema[], mapping: Record<string, string>): string {
  const keys = new Set(Object.keys(mapping));
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
}

function MappingEditor({
  connector,
  schemas,
  slug,
  onSlug,
  value,
  onChange,
  remote,
  suggestedValues,
}: {
  connector: ConnectorInfo;
  schemas: MappingSchema[];
  slug: string;
  onSlug: (slug: string) => void;
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  remote: RemoteField[] | null;
  /** ключ поля документа -> подобранное значение (бейдж «подобрано», пока значение не менялось) */
  suggestedValues: Record<string, string>;
}) {
  const free = FREE_TARGET[connector.kind];
  const withTables = connector.kind === "onec_odata";
  const sheets = connector.kind === "google_sheets";
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
          onChange={(e) => onSlug(e.target.value)}
        >
          {schemas.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.title}
            </option>
          ))}
        </Field>
      </div>
      {remote ? (
        <datalist id={DATALIST_ID}>
          {remote.map((f) => (
            <option key={f.code} value={f.code}>
              {f.title}
            </option>
          ))}
        </datalist>
      ) : null}
      <ul className="flex flex-col border-2 border-ink bg-card">
        {rows.map((r, i) => {
          const id = `map-${i}`;
          const included = Object.hasOwn(value, r.key);
          const current = value[r.key] ?? "";
          const suggested = Object.hasOwn(suggestedValues, r.key) && suggestedValues[r.key] === current && current !== "";
          const cell = "min-h-11 w-full border-2 border-ink bg-paper px-3 py-2 text-base";
          return (
            <li
              key={r.key}
              className="grid gap-2 border-t-2 border-ink p-3 first:border-t-0 sm:grid-cols-2 sm:items-center sm:gap-4"
            >
              {sheets ? (
                <label className="inline-flex min-h-11 min-w-0 items-center gap-3">
                  <input
                    type="checkbox"
                    checked={included}
                    onChange={(e) => {
                      const next = { ...value };
                      if (e.target.checked) next[r.key] = "";
                      else delete next[r.key];
                      onChange(next);
                    }}
                    className="size-5 shrink-0 accent-ink"
                  />
                  <span className="min-w-0">
                    <span className="block font-mono text-xs text-muted">Выгружать поле документа</span>
                    <span className="block break-words font-medium">{r.label}</span>
                  </span>
                </label>
              ) : (
                <label htmlFor={id} className="min-w-0">
                  <span className="block font-mono text-xs text-muted">Поле документа</span>
                  <span className="block break-words font-medium">{r.label}</span>
                  <span className="sr-only"> — {free ? free.label : "поле системы"}</span>
                </label>
              )}
              {free ? (
                <input
                  id={id}
                  type="text"
                  value={current}
                  maxLength={200}
                  autoComplete="off"
                  disabled={sheets && !included}
                  placeholder={sheets ? `по умолчанию: ${r.label}` : free.placeholder}
                  list={remote ? DATALIST_ID : undefined}
                  aria-label={sheets ? `Название колонки для «${r.label}»` : undefined}
                  onChange={(e) => onChange({ ...value, [r.key]: e.target.value })}
                  className={`${cell} disabled:opacity-50`}
                />
              ) : (
                <select
                  id={id}
                  value={current}
                  onChange={(e) => onChange({ ...value, [r.key]: e.target.value })}
                  className={cell}
                >
                  <option value="">не выгружать</option>
                  {connector.target_fields.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.label}
                    </option>
                  ))}
                </select>
              )}
              {suggested ? (
                <p className="font-mono text-xs text-accent-text sm:col-start-2">
                  Подобрано автоматически: проверьте и сохраните.
                </p>
              ) : null}
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
  const [slug, setSlug] = useState(() => initialSchema(schemas, destination?.mapping ?? {}));
  const [remote, setRemote] = useState<RemoteField[] | null>(null);
  const [applied, setApplied] = useState<Applied | null>(null);
  const [remoteMsg, setRemoteMsg] = useState<{ text: string; error: boolean } | null>(null);
  const [loadingRemote, startRemote] = useTransition();
  // актуальное сопоставление и схема для подстановки после ответа сервера (пока шёл запрос, пользователь мог править)
  const latest = useRef({ mapping, slug });
  latest.current = { mapping, slug };
  const editing = Boolean(destination);
  const savedSecrets = Boolean(destination?.has_secrets);

  const cfg = (f: FieldDef): string => {
    const fromState = state.config?.[f.name];
    if (fromState !== undefined) return fromState;
    const v = destination?.config[f.name];
    return v === undefined || v === null ? "" : String(v);
  };
  const mainFields = connector.config_fields.filter((f) => !f.advanced);
  const advancedFields = connector.config_fields.filter((f) => f.advanced);
  // редкие поля свёрнуты, но раскрыты, если в них уже что-то есть или ошибка: спрятанное значение путает
  const advancedOpen = advancedFields.some((f) => cfg(f) !== "" || Boolean(state.fieldErrors?.[f.name]));

  // Таблицы/CRM: только поля выбранной схемы (чужие ключи от просмотренных схем не отправляем); 1С как есть.
  // Таблицы хранят и пустое название колонки (поле включено), остальные - только заполненные.
  const sheets = connector.kind === "google_sheets";
  const allowed = new Set((schemas.find((s) => s.slug === slug)?.rows ?? []).map((r) => r.key));
  const scoped = connector.kind === "onec_odata" ? Object.keys(mapping) : Object.keys(mapping).filter((k) => allowed.has(k));
  const mappingJson = JSON.stringify(
    Object.fromEntries(scoped.filter((k) => sheets || mapping[k].trim() !== "").map((k) => [k, mapping[k]])),
  );

  const showMapping = hasMapping(connector);
  const remoteLabel = connector.label;
  // предложения подмешиваются в пустые строки текущей схемы; заполненные не трогаем
  const pullFields = () => {
    if (!destination) return;
    const reqSlug = slug;
    startRemote(async () => {
      setRemoteMsg(null);
      const res = await fetchRemoteFields(destination.id, reqSlug);
      if (!res.ok) {
        setRemoteMsg({ text: res.message, error: true });
        return;
      }
      setRemote(res.fields);
      const cur = latest.current;
      if (cur.slug !== reqSlug) {
        setRemoteMsg({ text: `Поля загружены (${res.fields.length}), но схема документа сменилась: нажмите кнопку ещё раз.`, error: false });
        return;
      }
      const rows = (schemas.find((s) => s.slug === reqSlug)?.rows ?? []).filter(
        (r) => connector.kind === "onec_odata" || !r.group,
      );
      const next = { ...cur.mapping };
      const values: Record<string, string> = {};
      for (const r of rows) {
        const code = res.suggested[r.key];
        if (!code) continue;
        // пустая строка: нет ключа, пустое значение или (Таблицы) поле отмечено без названия
        if (Object.hasOwn(next, r.key) && next[r.key].trim() !== "") continue;
        next[r.key] = code;
        values[r.key] = code;
      }
      const n = Object.keys(values).length;
      setMapping(next);
      setApplied({ slug: reqSlug, values, saveState: state });
      setRemoteMsg({
        text: `Подобрано ${n} из ${rows.length}; полей в ${remoteLabel}: ${res.fields.length}.`,
        error: false,
      });
    });
  };
  const suggestedValues = applied && applied.slug === slug && applied.saveState === state ? applied.values : {};

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
      <div role="status" aria-live="polite">
        {state.saved ? <p className="border-2 border-ink bg-card p-4 font-medium">Сохранено.</p> : null}
      </div>

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
        {mainFields.map((f) => (
          <ConfigInput key={f.name} f={f} value={cfg(f)} error={state.fieldErrors?.[f.name]} />
        ))}
        {advancedFields.length > 0 ? (
          <details open={advancedOpen} className="group border-2 border-ink bg-card">
            <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 font-semibold">
              <span aria-hidden="true" className="font-mono transition-transform group-open:rotate-90">
                ›
              </span>
              Дополнительно
              <span className="font-mono text-sm font-normal text-muted">обычно не нужно</span>
            </summary>
            <div className="flex flex-col gap-4 border-t-2 border-ink p-4">
              {advancedFields.map((f) => (
                <ConfigInput key={f.name} f={f} value={cfg(f)} error={state.fieldErrors?.[f.name]} />
              ))}
            </div>
          </details>
        ) : null}
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

      {showMapping ? (
        <section aria-labelledby="dest-map" className="flex flex-col gap-4">
          <h2 id="dest-map" className="text-2xl font-semibold tracking-tight">
            Сопоставление полей
          </h2>
          {destination ? (
            <div className="flex max-w-3xl flex-col gap-2">
              <p className="text-sm text-muted">Берём поля по сохранённым настройкам подключения.</p>
              <div>
                <Button type="button" variant="ghost" onClick={pullFields} disabled={loadingRemote || pending}>
                  {loadingRemote ? "Читаем поля…" : `Подтянуть поля из ${connector.label}`}
                </Button>
              </div>
              <div role="alert">
                {remoteMsg?.error ? (
                  <p className="break-words border-2 border-accent-text bg-paper p-3 font-medium text-accent-text">
                    {remoteMsg.text}
                  </p>
                ) : null}
              </div>
              <div role="status" aria-live="polite">
                {remoteMsg && !remoteMsg.error ? <p className="border-2 border-ink bg-card p-3">{remoteMsg.text}</p> : null}
              </div>
            </div>
          ) : (
            <p className="max-w-3xl text-sm text-muted">
              Чтобы подтянуть поля из системы, сначала сохраните подключение.
            </p>
          )}
          <MappingEditor
            connector={connector}
            schemas={schemas}
            slug={slug}
            onSlug={setSlug}
            value={mapping}
            onChange={setMapping}
            remote={remote}
            suggestedValues={suggestedValues}
          />
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
