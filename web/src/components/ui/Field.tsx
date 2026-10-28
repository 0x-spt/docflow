import type { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

type Shared = { label: string; name: string; error?: string; hint?: string };
type InputProps = Shared & { as?: "input" } & Omit<InputHTMLAttributes<HTMLInputElement>, "name">;
type TextareaProps = Shared & { as: "textarea" } & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "name">;
type SelectProps = Shared & { as: "select" } & Omit<SelectHTMLAttributes<HTMLSelectElement>, "name">;

const CONTROL =
  "min-h-11 w-full border-2 border-ink bg-card px-3 py-2 font-sans text-base text-ink " +
  "aria-[invalid=true]:border-accent-text";

/** Подпись + поле + текст ошибки, связанные через `htmlFor` и `aria-describedby`. */
export function Field(props: InputProps | TextareaProps | SelectProps) {
  const { label, name, error, hint } = props;
  const id = `f-${name}`;
  const errId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy = [error ? errId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined;
  const common = { id, name, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined };

  // собственные пропсы Field не должны попасть в DOM-элемент
  const { label: _label, name: _name, error: _error, hint: _hint, ...rest } = props;
  void [_label, _name, _error, _hint];
  const { className = "", ...attrs } = rest as { className?: string };
  const cls = `${CONTROL} ${className}`;

  let control;
  if (props.as === "textarea") {
    const { as: _as, ...el } = attrs as TextareaProps;
    void _as;
    control = <textarea rows={4} {...el} {...common} className={cls} />;
  } else if (props.as === "select") {
    const { as: _as, ...el } = attrs as SelectProps;
    void _as;
    control = <select {...el} {...common} className={cls} />;
  } else {
    const { as: _as, ...el } = attrs as InputProps;
    void _as;
    control = <input {...el} {...common} className={cls} />;
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-mono text-sm font-medium">
        {label}
      </label>
      {control}
      {hint ? (
        <p id={hintId} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errId} role="alert" className="text-sm font-medium text-accent-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
