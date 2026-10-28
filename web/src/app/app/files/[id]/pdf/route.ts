import { isDocId } from "@/lib/ids";
import { proxyFile } from "@/lib/proxy";

/** PDF документа через сайт: браузер не знает токена, сайт подставляет сессию. */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isDocId(id)) return new Response("Файл не найден", { status: 404 });
  return proxyFile(`/v1/documents/${encodeURIComponent(id)}/pdf`);
}
