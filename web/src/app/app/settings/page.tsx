import Link from "next/link";

import { CreateKeyForm } from "@/components/app/CreateKeyForm";
import { CreateWebhookForm } from "@/components/app/CreateWebhookForm";
import { UsageBlock } from "@/components/app/UsageBlock";
import { Section } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { isUuid } from "@/lib/ids";
import { apiFetchAuthed, requireMe } from "@/lib/session";
import type { ApiKey, Webhook } from "@/lib/types";

import { deleteWebhook, revokeKey } from "./actions";

export const metadata = { title: "API и вебхуки — DocFlow" };

const SMALL_BTN =
  "inline-flex min-h-11 items-center px-2 font-medium underline decoration-2 underline-offset-4 hover:text-accent-text";

function publicApiUrl(): string {
  return (process.env.DOCFLOW_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

const ERRORS: Record<string, string> = {
  key_forbidden: "Ключами управляет только владелец аккаунта.",
  hook_forbidden: "Вебхуками управляет только владелец аккаунта.",
  key_failed: "Не удалось отозвать ключ. Проверьте соединение и попробуйте ещё раз.",
  hook_failed: "Не удалось удалить вебхук. Проверьте соединение и попробуйте ещё раз.",
};

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const errCode = one(sp.err);
  const errorText = errCode && Object.hasOwn(ERRORS, errCode) ? ERRORS[errCode] : null;
  // подтверждение без JS-диалогов: ?confirm=key:<uuid> или ?confirm=webhook:<uuid>
  const [confirmKind, confirmId] = (one(sp.confirm) ?? "").split(":");
  const confirming = confirmId && isUuid(confirmId) ? { kind: confirmKind, id: confirmId } : null;
  const me = await requireMe();
  const isOwner = me.user?.role === "owner";
  const [keys, hooks] = await Promise.all([
    isOwner ? apiFetchAuthed<ApiKey[]>("/v1/api-keys") : Promise.resolve<ApiKey[]>([]),
    apiFetchAuthed<Webhook[]>("/v1/webhooks"),
  ]);
  const api = publicApiUrl();
  const curl = [
    `curl -X POST ${api}/v1/documents \\`,
    `  -H "Authorization: Bearer dfk_ВАШ_КЛЮЧ" \\`,
    `  -H "Idempotency-Key: $(uuidgen)" \\`,
    `  -F schema=invoice_ru \\`,
    `  -F mode=gray \\`,
    `  -F files=@schet.jpg`,
  ].join("\n");

  return (
    <div className="flex flex-col">
      <div className="mb-4">
        <h1 className="text-4xl font-semibold tracking-tight">API и вебхуки</h1>
        <p className="mt-1 text-muted">Ключи API и вебхуки: подключите DocFlow к своей учётной системе.</p>
      </div>

      <div className="mb-4">
        <UsageBlock />
      </div>

      {errorText ? (
        <div role="alert" className="mb-4 border-2 border-accent-text bg-paper p-4 font-medium text-accent-text">
          {errorText}
        </div>
      ) : null}

      <Section num="01" title="Ключи API" meta={isOwner ? `${keys.filter((k) => !k.revoked_at).length} активных` : undefined}>
        {isOwner ? (
          <div className="flex flex-col gap-4">
            {keys.length === 0 ? (
              <p className="border-2 border-ink bg-card p-4">Ключей пока нет.</p>
            ) : (
              <div className="relative overflow-x-auto border-2 border-ink bg-card">
                <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
                  <thead className="bg-ink text-paper">
                    <tr>
                      {["Ключ", "Создан", "Состояние", ""].map((h, i) => (
                        <th key={i} scope="col" className="px-3 py-2 font-mono font-medium">
                          {h || <span className="sr-only">Действия</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {keys.map((k) => (
                      <tr key={k.id} className="border-t-2 border-ink align-top">
                        <td className="px-3 py-2.5 font-mono">{k.prefix}…</td>
                        <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums">
                          {formatDateTime(k.created_at)}
                        </td>
                        <td className="px-3 py-2.5">
                          {k.revoked_at ? (
                            <span className="text-muted">Отозван {formatDateTime(k.revoked_at)}</span>
                          ) : (
                            "Действует"
                          )}
                        </td>
                        <td className="px-3 py-1.5">
                          {k.revoked_at ? null : confirming?.kind === "key" && confirming.id === k.id ? (
                            <div className="flex flex-wrap items-center gap-2">
                              <form action={revokeKey.bind(null, k.id)}>
                                <button type="submit" className={SMALL_BTN}>
                                  Точно отозвать?
                                </button>
                              </form>
                              <Link href="/app/settings" className={SMALL_BTN}>
                                Отмена
                              </Link>
                            </div>
                          ) : (
                            <Link
                              href={`/app/settings?confirm=key:${k.id}`}
                              className={SMALL_BTN}
                              aria-label={`Отозвать ключ ${k.prefix}`}
                            >
                              Отозвать
                            </Link>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <CreateKeyForm />
          </div>
        ) : (
          <p className="border-2 border-ink bg-card p-4">Ключами API управляет владелец аккаунта.</p>
        )}
      </Section>

      <Section num="02" title="Вебхуки" meta={`${hooks.length} активных`}>
        <div className="flex flex-col gap-4">
          {hooks.length === 0 ? (
            <p className="border-2 border-ink bg-card p-4">Вебхуков пока нет.</p>
          ) : (
            <div className="relative overflow-x-auto border-2 border-ink bg-card">
              <table className="w-full min-w-[38rem] border-collapse text-left text-sm">
                <thead className="bg-ink text-paper">
                  <tr>
                    {["Адрес", "События", "Добавлен", ...(isOwner ? [""] : [])].map((h, i) => (
                      <th key={i} scope="col" className="px-3 py-2 font-mono font-medium">
                        {h || <span className="sr-only">Действия</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {hooks.map((h) => (
                    <tr key={h.id} className="border-t-2 border-ink align-top">
                      <td className="max-w-[20rem] break-all px-3 py-2.5 font-mono">{h.url}</td>
                      <td className="px-3 py-2.5 font-mono text-xs leading-5">
                        {h.events.map((e) => (
                          <div key={e}>{e}</div>
                        ))}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums">
                        {formatDateTime(h.created_at)}
                      </td>
                      {isOwner ? (
                        <td className="px-3 py-1.5">
                          {confirming?.kind === "webhook" && confirming.id === h.id ? (
                            <div className="flex flex-wrap items-center gap-2">
                              <form action={deleteWebhook.bind(null, h.id)}>
                                <button type="submit" className={SMALL_BTN}>
                                  Точно удалить?
                                </button>
                              </form>
                              <Link href="/app/settings" className={SMALL_BTN}>
                                Отмена
                              </Link>
                            </div>
                          ) : (
                            <Link
                              href={`/app/settings?confirm=webhook:${h.id}`}
                              className={SMALL_BTN}
                              aria-label={`Удалить вебхук ${h.url}`}
                            >
                              Удалить
                            </Link>
                          )}
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {isOwner ? (
            <CreateWebhookForm />
          ) : (
            <p className="border-2 border-ink bg-card p-4">Вебхуками управляет владелец аккаунта.</p>
          )}
        </div>
      </Section>

      <Section num="03" title="Загрузка по API">
        <p className="mb-3 max-w-3xl text-muted">
          Адрес API: <code className="font-mono">{api}</code>. Пример загрузки документа ключом API. Результат придёт на вебхук или по <code className="font-mono">GET /v1/documents/{"{id}"}</code>.
        </p>
        <pre className="overflow-x-auto border-2 border-ink bg-card p-4 font-mono text-sm leading-6">{curl}</pre>
      </Section>

      <Section num="04" title="Аккаунт">
        <dl className="grid max-w-2xl grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 border-2 border-ink bg-card p-4">
          <dt className="font-mono text-sm text-muted">Email</dt>
          <dd className="break-all">{me.user?.email ?? "—"}</dd>
          <dt className="font-mono text-sm text-muted">Роль</dt>
          <dd>{isOwner ? "Владелец" : "Оператор"}</dd>
          <dt className="font-mono text-sm text-muted">Клиент</dt>
          <dd>{me.tenant.name}</dd>
          <dt className="font-mono text-sm text-muted">Модель</dt>
          <dd>{me.tenant.extractor === "ollama" ? "Локальная" : "Облачная"}</dd>
          <dt className="font-mono text-sm text-muted">Автоподтверждение</dt>
          <dd>{me.tenant.auto_ok ? "Включено" : "Выключено"}</dd>
        </dl>
        <form method="post" action="/app/logout" className="mt-4">
          <button
            type="submit"
            className="inline-flex min-h-11 items-center border-2 border-ink bg-card px-5 font-semibold shadow-hard active:translate-x-1.5 active:translate-y-1.5 active:shadow-none"
          >
            Выйти
          </button>
        </form>
      </Section>
    </div>
  );
}
