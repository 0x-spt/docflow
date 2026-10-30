// Транслит для имён полей и slug: «Номер договора» -> nomer_dogovora.
// Из ../types — только import type, соседи — с явным .ts (так модуль запускает node --test без сборки).

const MAP: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k",
  л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts",
  ч: "ch", ш: "sh", щ: "shch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
};

export function translit(text: string): string {
  let out = "";
  for (const ch of text.toLowerCase()) out += MAP[ch] ?? ch;
  return out;
}

/** Идентификатор из текста; пустой результат остаётся пустым (решает вызывающий). */
function identifierOrEmpty(text: string, maxLen: number): string {
  const id = translit(text)
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return id.slice(0, maxLen).replace(/_+$/, "");
}

export function toIdentifier(text: string, maxLen = 40): string {
  return identifierOrEmpty(text, maxLen) || "field";
}

/** base, иначе base_2, base_3… — основа укорачивается, чтобы с суффиксом влезть в maxLen. */
export function uniqueName(base: string, taken: ReadonlySet<string>, maxLen = 40): string {
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) {
    const suffix = `_${n}`;
    const cand = base.slice(0, maxLen - suffix.length).replace(/_+$/, "") + suffix;
    if (!taken.has(cand)) return cand;
  }
}

/** slug типа документа: ^[a-z0-9_]{2,40}$ */
export function slugFromTitle(title: string): string {
  const id = identifierOrEmpty(title, 40);
  if (!id) return "doc";
  return id.length === 1 ? `${id}_doc` : id;
}
