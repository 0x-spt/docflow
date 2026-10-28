import Link from "next/link";
import { notFound } from "next/navigation";

import { addAlias, addEntry, deleteDictionary } from "../actions";
import { DictionaryEditor } from "@/components/app/DictionaryEditor";
import { Button } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { apiFetchAuthed } from "@/lib/session";
import type { Dictionary, Suggestion } from "@/lib/types";

export const metadata = { title: "Справочник — DocFlow" };

const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";
const NAME_RE = /^[a-z0-9_]{2,40}$/;

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export default async function DictionaryPage({
  params,
  searchParams,
}: {
  params: Promise<{ name: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { name } = await params;
  if (!NAME_RE.test(name)) notFound();
  const sp = await searchParams;
  const error = one(sp.error);
  const confirmDelete = one(sp.confirm) === "delete";

  let dict: Dictionary;
  let suggestions: Suggestion[];
  try {
    [dict, suggestions] = await Promise.all([
      apiFetchAuthed<Dictionary>(`/v1/dictionaries/${name}`),
      apiFetchAuthed<Suggestion[]>(`/v1/dictionaries/${name}/suggestions`),
    ]);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const entries = dict.entries ?? [];

  return (
    <div className="flex flex-col gap-8">
      <div>
        <Link href="/app/dictionaries" className={`inline-flex min-h-11 items-center ${LINK}`}>
          ← Все справочники
        </Link>
        <h1 className="text-4xl font-semibold tracking-tight">{dict.title}</h1>
        <p className="mt-1 font-mono text-sm text-muted">{dict.name}</p>
      </div>

      {error ? (
        <div role="alert" className="border-2 border-accent-text bg-paper p-4 font-medium text-accent-text">
          {error}
        </div>
      ) : null}

      <DictionaryEditor key={dict.updated_at ?? "new"} name={dict.name} title={dict.title} entries={entries} />

      <section aria-labelledby="sugg" className="flex flex-col gap-3 border-t-2 border-ink pt-8">
        <h2 id="sugg" className="text-2xl font-semibold tracking-tight">
          Подсказки
        </h2>
        <p className="max-w-3xl text-muted">
          Значения из последних документов, которых не оказалось в справочнике или которые пришлось исправить.
          Несохранённые правки в таблице выше при добавлении подсказки не учитываются.
        </p>
        {suggestions.length === 0 ? (
          <p className="border-2 border-ink bg-card p-4">Подсказок нет: все значения в последних документах найдены.</p>
        ) : (
          <div className="relative overflow-x-auto border-2 border-ink bg-card">
            <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
              <thead className="bg-ink text-paper">
                <tr>
                  {["Значение", "Что произошло", "Раз", "Действие"].map((h) => (
                    <th key={h} scope="col" className="px-3 py-2 font-mono font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {suggestions.map((s) => {
                  const known = s.nearest !== null && entries.some((e) => e.value === s.nearest);
                  const inList = entries.some((e) => e.value === s.value || e.aliases.includes(s.value));
                  return (
                    <tr key={`${s.code}-${s.value}`} className="border-t-2 border-ink align-top">
                      <td className="px-3 py-2.5 font-medium">{s.value}</td>
                      <td className="px-3 py-2.5">
                        {s.code === "corrected" ? (
                          <>исправлено на «{s.nearest}»</>
                        ) : s.nearest ? (
                          <>нет в справочнике, похоже на «{s.nearest}»</>
                        ) : (
                          <>нет в справочнике</>
                        )}
                      </td>
                      <td className="px-3 py-2.5 font-mono tabular-nums">{s.count}</td>
                      <td className="px-3 py-1.5">
                        {inList ? (
                          <span className="inline-flex min-h-11 items-center text-muted">Уже в справочнике</span>
                        ) : (
                          <div className="flex flex-col items-start gap-1">
                            {known && s.nearest ? (
                              <form action={addAlias.bind(null, dict.name, s.value, s.nearest)}>
                                <button type="submit" className={`inline-flex min-h-11 items-center text-left font-medium ${LINK}`}>
                                  Добавить как написание к «{s.nearest}»
                                </button>
                              </form>
                            ) : null}
                            <form action={addEntry.bind(null, dict.name, s.value)}>
                              <button type="submit" className={`inline-flex min-h-11 items-center text-left font-medium ${LINK}`}>
                                Добавить новым значением
                              </button>
                            </form>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="danger" className="flex flex-col gap-3 border-t-2 border-ink pt-8">
        <h2 id="danger" className="text-2xl font-semibold tracking-tight">
          Удаление
        </h2>
        {confirmDelete ? (
          <div className="flex max-w-xl flex-col gap-4 border-2 border-ink bg-card p-4">
            <p>
              Справочник «{dict.title}» ({entries.length} записей) будет удалён. Поля схем, которые на него ссылаются,
              перестанут сверяться.
            </p>
            <div className="flex flex-wrap gap-4">
              <form action={deleteDictionary.bind(null, dict.name)}>
                <Button type="submit" variant="accent">
                  Точно удалить?
                </Button>
              </form>
              <Button href={`/app/dictionaries/${dict.name}`} variant="ghost">
                Отмена
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Button href={`/app/dictionaries/${dict.name}?confirm=delete`} variant="ghost">
              Удалить справочник
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}
