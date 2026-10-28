import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "accent" | "ghost";

const BASE =
  "inline-flex min-h-11 items-center justify-center gap-2 border-2 border-ink px-5 py-2.5 " +
  "font-sans text-base font-semibold shadow-hard transition-transform " +
  "active:translate-x-1.5 active:translate-y-1.5 active:shadow-none " +
  "disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-ink text-paper",
  accent: "bg-accent text-ink",
  ghost: "bg-card text-ink",
};

type Common = { variant?: Variant; className?: string; children: ReactNode };
type AsLink = Common & { href: string } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href" | "className" | "children">;
type AsButton = Common & { href?: undefined } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children">;

/** Кнопка; с `href` — ссылка того же вида. */
export function Button(props: AsLink | AsButton) {
  const { variant = "primary", className = "", children, ...rest } = props;
  const cls = `${BASE} ${VARIANTS[variant]} ${className}`;
  if (rest.href !== undefined) {
    return (
      <Link {...(rest as Omit<AsLink, keyof Common>)} className={cls}>
        {children}
      </Link>
    );
  }
  const { type = "button", ...buttonRest } = rest as Omit<AsButton, keyof Common>;
  return (
    <button {...buttonRest} type={type} className={cls}>
      {children}
    </button>
  );
}
