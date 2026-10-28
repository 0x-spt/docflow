import Link from "next/link";

import { CreateDictionaryForm } from "@/components/app/CreateDictionaryForm";
import { formatDateTime } from "@/lib/format";
import { apiFetchAuthed } from "@/lib/session";
import type { Dictionary } from "@/lib/types";

export const metadata = { title: "Справочники — DocFlow" };

const LINK = "underline decoration-2 underline-offset-4 hover:text-accent-text";

export default async function DictionariesPage() {
  const dictionaries = await apiFetchAuthed<Dictionary[]>("/v1/dictionaries");
  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-4xl font-semibold tracking-tight">Справочники</h1>
        <p className="mt-1 max-w-3xl text-muted">
          Списки ваших клиник, врачей, контрагентов. Поля схемы сверяются со справочником: близкие написания
          исправляются на значение из списка, незнакомые уходят на проверку.
        </p>
      </div>

      {dictionaries.length === 0 ? (
        <div className="border-2 border-ink bg-card p-6">
          <p className="text-lg font-medium">Справочников пока нет.</p>
          <p className="mt-1 text-muted">Создайте первый ниже и заполните его записями.</p>
        </div>
      ) : (
        <div className="relative overflow-x-auto border-2 border-ink bg-card">
          <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
            <thead className="bg-ink text-paper">
              <tr>
                {["Название", "Имя", "Записей", "Изменён"].map((h) => (
                  <th key={h} scope="col" className="px-3 py-2 font-mono font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dictionaries.map((d) => (
                <tr key={d.name} className="border-t-2 border-ink align-top hover:bg-paper">
                  <td className="px-3 py-1.5">
                    <Link
                      href={`/app/dictionaries/${d.name}`}
                      className={`inline-flex min-h-11 items-center text-base font-medium ${LINK}`}
                    >
                      {d.title}
                    </Link>
                  </td>
                  <td className="px-3 py-2.5 font-mono">{d.name}</td>
                  <td className="px-3 py-2.5 font-mono tabular-nums">{d.size}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums">{formatDateTime(d.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <section aria-labelledby="new-dict" className="flex flex-col gap-3">
        <h2 id="new-dict" className="text-2xl font-semibold tracking-tight">
          Новый справочник
        </h2>
        <CreateDictionaryForm />
      </section>
    </div>
  );
}
