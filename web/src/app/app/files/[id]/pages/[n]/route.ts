import { isDocId } from "@/lib/ids";
import { proxyFile } from "@/lib/proxy";

/** JPEG-превью страницы документа (n с 1). Превью нет (документ старше превью) — 404, кабинет покажет плашку. */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string; n: string }> }) {
  const { id, n } = await params;
  if (!isDocId(id) || !/^[1-9]\d{0,3}$/.test(n)) return new Response("Страница не найдена", { status: 404 });
  return proxyFile(`/v1/documents/${encodeURIComponent(id)}/pages/${n}`);
}
