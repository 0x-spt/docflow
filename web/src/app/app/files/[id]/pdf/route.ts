import { proxyFile } from "@/lib/proxy";

/** PDF документа через сайт: браузер не знает токена, сайт подставляет сессию. */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxyFile(`/v1/documents/${encodeURIComponent(id)}/pdf`);
}
