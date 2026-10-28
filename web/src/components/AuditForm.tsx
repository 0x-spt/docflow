"use client";

import Link from "next/link";
import { useActionState, useEffect, useRef } from "react";

import { submitLead } from "@/app/audit/actions";
import { Button, Field } from "@/components/ui";
import { INITIAL_LEAD_STATE, NICHES, TARGETS, VOLUMES } from "@/lib/leads";

/** Форма заявки на аудит. Без JS — обычная отправка в server action, с JS — ошибки у полей и блокировка кнопки. */
export function AuditForm() {
  const [state, action, pending] = useActionState(submitLead, INITIAL_LEAD_STATE);
  const doneRef = useRef<HTMLHeadingElement>(null);
  const messageRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  // после ответа сервера фокус — на результат: заголовок успеха, первое ошибочное поле или общее сообщение
  useEffect(() => {
    if (state.ok) doneRef.current?.focus();
    else if (state.errors && Object.keys(state.errors).length) {
      formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
    } else if (state.message) messageRef.current?.focus();
  }, [state]);

  if (state.ok) {
    return (
      <div role="status" className="border-2 border-ink bg-card p-6 shadow-hard sm:p-10">
        <p className="font-mono text-sm uppercase text-accent-text">Заявка отправлена</p>
        <h3 ref={doneRef} tabIndex={-1} className="mt-4 text-4xl font-semibold tracking-tight sm:text-5xl">
          Заявка принята
        </h3>
        <p className="mt-4 max-w-prose text-lg">
          Ответим в течение [СРОК ОТВЕТА] по контакту, который вы оставили: договоримся о времени и попросим
          несколько примеров документов.
        </p>
      </div>
    );
  }

  const v = state.values ?? {};
  const e = state.errors ?? {};
  const checkedTargets = new Set((v.targets ?? "").split(","));

  return (
    // key: после ответа сервера поля пересоздаются с введёнными значениями (React сбрасывает форму после action)
    <form
      key={JSON.stringify(state)}
      ref={formRef}
      action={action}
      noValidate
      aria-busy={pending}
      className="relative grid gap-6 border-2 border-ink bg-card p-5 shadow-hard sm:p-8"
    >
      {state.message ? (
        <div
          ref={messageRef}
          tabIndex={-1}
          role="alert"
          className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text"
        >
          {state.message}
        </div>
      ) : null}

      <div className="grid gap-6 md:grid-cols-2">
        <Field
          label="Имя *"
          name="name"
          autoComplete="name"
          required
          maxLength={100}
          defaultValue={v.name}
          error={e.name}
        />
        <Field label="Компания" name="company" autoComplete="organization" maxLength={200} defaultValue={v.company} />
      </div>

      <Field
        label="Контакт *"
        name="contact"
        required
        maxLength={200}
        hint="Телефон, Telegram или email — как вам удобнее."
        defaultValue={v.contact}
        error={e.contact}
      />

      <div className="grid gap-6 md:grid-cols-2">
        <Field as="select" label="Ниша" name="niche" defaultValue={v.niche ?? ""}>
          <option value="">Не выбрано</option>
          {NICHES.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </Field>
        <Field
          as="select"
          label="Документов в месяц *"
          name="volume"
          required
          defaultValue={v.volume ?? ""}
          error={e.volume}
        >
          <option value="" disabled>
            Выберите
          </option>
          {VOLUMES.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Field>
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-3 font-mono text-sm font-medium">Куда выгружать данные</legend>
        <div className="grid grid-cols-1 gap-x-6 gap-y-1 min-[400px]:grid-cols-2 lg:grid-cols-3">
          {TARGETS.map((t) => (
            <label key={t.value} className="flex min-h-11 cursor-pointer items-center gap-3 text-base">
              <input
                type="checkbox"
                name="targets"
                value={t.value}
                defaultChecked={checkedTargets.has(t.value)}
                className="df-check size-6 shrink-0"
              />
              {t.label}
            </label>
          ))}
        </div>
        <p className="text-sm text-muted">Можно выбрать несколько.</p>
      </fieldset>

      <Field
        as="textarea"
        rows={3}
        label="Какие документы"
        name="documents"
        maxLength={2000}
        hint="Например: заказ-наряды от руки, счета, ТТН."
        defaultValue={v.documents}
      />
      <Field
        as="textarea"
        rows={3}
        label="Что ещё важно"
        name="message"
        maxLength={2000}
        hint="Где хранятся документы сейчас, кто их вносит, сроки."
        defaultValue={v.message}
      />

      {/* приманка для ботов: вне экрана и inert — человек и автозаполнение её не трогают; в API уходит как website */}
      <div inert className="absolute -left-[10000px] top-auto h-px w-px overflow-hidden">
        <label htmlFor="f-hp_field_x">Не заполняйте это поле</label>
        <input id="f-hp_field_x" name="hp_field_x" type="text" tabIndex={-1} autoComplete="off" defaultValue="" />
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-start gap-3">
          <input
            id="f-consent"
            name="consent"
            type="checkbox"
            required
            defaultChecked={v.consent === "on"}
            aria-invalid={e.consent ? true : undefined}
            aria-describedby={e.consent ? "f-consent-error" : undefined}
            className="df-check mt-0.5 size-6 shrink-0 aria-[invalid=true]:border-accent-text"
          />
          <label htmlFor="f-consent" className="cursor-pointer text-base leading-snug">
            Согласен на обработку персональных данных по{" "}
            <Link href="/privacy" className="underline decoration-2 underline-offset-4 hover:text-accent-text">
              политике обработки ПД
            </Link>
            . *
          </label>
        </div>
        {e.consent ? (
          <p id="f-consent-error" role="alert" className="pl-9 text-sm font-medium text-accent-text">
            {e.consent}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <Button type="submit" variant="accent" disabled={pending} className="w-full sm:w-auto">
          {pending ? "Отправляем…" : "Отправить заявку"}
        </Button>
        <p className="font-mono text-sm text-muted">* — обязательные поля</p>
      </div>
    </form>
  );
}
