/** `next` принимаем только как путь внутри кабинета: `/app…`, не `//…` и не с обратным слэшем (защита от open redirect). */
export function safeNext(value: unknown): string {
  if (typeof value !== "string") return "/app";
  if (!/^\/app(?:[/?#]|$)/.test(value)) return "/app";
  if (value.startsWith("//") || value.includes("\\")) return "/app";
  return value;
}
