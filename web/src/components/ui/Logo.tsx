import Link from "next/link";

/** Логотип «DocFlow»; ведёт на главную. */
export function Logo({ className = "" }: { className?: string }) {
  return (
    <Link
      href="/"
      aria-label="DocFlow, на главную"
      className={`inline-flex min-h-11 items-center text-xl font-semibold tracking-tight ${className}`}
    >
      DocFlow
    </Link>
  );
}
