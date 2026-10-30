"use client";

import { useState, useTransition } from "react";

import {
  deleteMailbox,
  saveMailbox,
  testMailbox,
} from "@/app/app/mail/actions";
import { Button, Field } from "@/components/ui";
import { isOpenToAll, parseSenders } from "@/lib/channels";
import { formatDateTime } from "@/lib/format";
import type { MailboxInfo } from "@/lib/types";

const ALERT =
  "break-words border-2 border-accent-text bg-paper p-4 font-medium text-accent-text";

type Outcome = { ok: boolean; message: string };

/** Почтовый ящик: настройки IMAP, проверка входа, отключение. Пароль уходит только в server action и стирается после сохранения. */
export function MailboxForm({
  mailbox,
  schemas,
}: {
  mailbox: MailboxInfo | null;
  schemas: { slug: string; title: string }[];
}) {
  const [host, setHost] = useState(mailbox?.host ?? "");
  const [port, setPort] = useState(String(mailbox?.port ?? 993));
  const [username, setUsername] = useState(mailbox?.username ?? "");
  const [password, setPassword] = useState("");
  const [folder, setFolder] = useState(mailbox?.folder ?? "INBOX");
  const [senders, setSenders] = useState(
    (mailbox?.allowed_senders ?? []).join("\n"),
  );
  const [schema, setSchema] = useState(mailbox?.default_schema ?? "");
  const [sendersError, setSendersError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [check, setCheck] = useState<Outcome | null>(null);
  const [pending, start] = useTransition();

  const open = isOpenToAll(parseSenders(senders).senders);

  function submit() {
    if (pending) return;
    setError(null);
    setSaved(false);
    const { errors } = parseSenders(senders);
    if (errors.length > 0) {
      setSendersError(
        `Не похоже на адрес или домен: ${errors.join(", ")}. Пример: ivan@firma.ru или @firma.ru`,
      );
      return;
    }
    setSendersError(null);
    start(async () => {
      const res = await saveMailbox({
        host,
        port,
        username,
        password,
        folder,
        senders,
        defaultSchema: schema,
      });
      if (res.error) setError(res.error);
      else {
        setPassword("");
        setSaved(true);
      }
    });
  }

  function runCheck() {
    if (pending) return;
    setCheck(null);
    start(async () => setCheck(await testMailbox()));
  }

  function remove() {
    if (
      pending ||
      !window.confirm("Отключить ящик? Письма перестанут приниматься.")
    )
      return;
    setError(null);
    start(async () => {
      const res = await deleteMailbox();
      if (res.error) setError(res.error);
    });
  }

  return (
    <div className="flex flex-col gap-6" aria-busy={pending}>
      {mailbox && !mailbox.active ? (
        <p role="alert" className={ALERT}>
          Приём писем остановлен: не удалось войти в ящик. Исправьте настройки
          (чаще всего — пароль приложения) и сохраните их.
        </p>
      ) : null}
      {mailbox?.last_error ? (
        <p role="alert" className={ALERT}>
          Последняя ошибка: {mailbox.last_error}
        </p>
      ) : null}
      {mailbox?.last_checked_at ? (
        <p className="font-mono text-sm text-muted">
          Последняя проверка: {formatDateTime(mailbox.last_checked_at)}
        </p>
      ) : null}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="flex max-w-xl flex-col gap-4 border-2 border-ink bg-card p-6"
      >
        <Field
          label="Почтовый сервер (IMAP)"
          name="mail-host"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="imap.example.ru"
          required
          autoComplete="off"
        />
        <Field
          label="Порт"
          name="mail-port"
          inputMode="numeric"
          value={port}
          onChange={(e) => setPort(e.target.value)}
          required
        />
        <Field
          label="Логин"
          name="mail-username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
          autoComplete="off"
        />
        <Field
          label="Пароль приложения"
          name="mail-password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={mailbox ? "оставьте пустым, чтобы не менять" : undefined}
          required={!mailbox}
          hint="Не пароль от почты, а отдельный пароль приложения из настроек почтового сервиса. Хранится в зашифрованном виде."
        />
        <Field
          label="Папка"
          name="mail-folder"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          hint="Латиницей, как на сервере: INBOX"
        />
        <Field
          label="Разрешённые отправители"
          name="mail-senders"
          as="textarea"
          value={senders}
          onChange={(e) => setSenders(e.target.value)}
          hint="Адреса или домены через запятую или с новой строки: ivan@firma.ru, @firma.ru"
          error={sendersError ?? undefined}
        />
        {open ? (
          <p
            role="status"
            className="border-2 border-ink bg-paper p-3 text-sm font-medium"
          >
            Письма принимаются от всех: спам будет тратить страницы
          </p>
        ) : null}
        <Field
          label="Тип документов по умолчанию"
          name="mail-schema"
          as="select"
          value={schema}
          onChange={(e) => setSchema(e.target.value)}
        >
          <option value="">Определять автоматически</option>
          {schemas.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.title}
            </option>
          ))}
        </Field>
        {error ? (
          <p
            role="alert"
            className="break-words text-sm font-medium text-accent-text"
          >
            {error}
          </p>
        ) : null}
        <div role="status" aria-live="polite">
          {saved ? <p className="font-medium">Сохранено.</p> : null}
        </div>
        <div>
          <Button type="submit" disabled={pending}>
            {pending ? "Сохраняем…" : mailbox ? "Сохранить" : "Подключить"}
          </Button>
        </div>
      </form>

      {mailbox ? (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-3">
            <Button variant="ghost" disabled={pending} onClick={runCheck}>
              Проверить
            </Button>
            <Button variant="ghost" disabled={pending} onClick={remove}>
              Отключить ящик
            </Button>
          </div>
          <div role="status" aria-live="polite">
            {check ? (
              <p
                className={`break-words border-2 p-3 text-sm font-medium ${
                  check.ok
                    ? "border-ink bg-card"
                    : "border-accent-text bg-paper text-accent-text"
                }`}
              >
                {check.message}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
