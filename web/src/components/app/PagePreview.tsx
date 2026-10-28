"use client";

import { useEffect, useRef, useState } from "react";

/** Плашка вместо превью: документ обработан до появления превью или файла нет. */
function PreviewUnavailable({ pdfHref }: { pdfHref: string | null }) {
  return (
    <div className="flex min-h-48 flex-col items-start justify-center gap-2 border-2 border-dashed border-ink bg-card p-5">
      <p className="font-medium">Превью недоступно — скачайте PDF</p>
      {pdfHref ? (
        <a
          href={pdfHref}
          download
          className="inline-flex min-h-11 items-center font-medium underline decoration-2 underline-offset-4 hover:text-accent-text"
        >
          Скачать PDF
        </a>
      ) : null}
    </div>
  );
}

/** Превью страницы через route handler сайта; ошибка загрузки → плашка со ссылкой на PDF. */
export function PagePreview({
  src,
  page,
  pages,
  pdfHref,
}: {
  src: string;
  page: number;
  pages: number;
  pdfHref: string | null;
}) {
  const [failed, setFailed] = useState(false);
  const img = useRef<HTMLImageElement>(null);

  // картинка могла упасть до гидратации, тогда onError уже не сработает
  useEffect(() => {
    const el = img.current;
    if (el && el.complete && el.naturalWidth === 0) setFailed(true);
  }, []);

  if (failed) return <PreviewUnavailable pdfHref={pdfHref} />;
  return (
    <figure className="border-2 border-ink bg-card">
      {/* размеры скана заранее неизвестны, оптимизатор next/image для приватных файлов не нужен */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={img}
        src={src}
        alt={`Страница ${page} из ${pages}`}
        onError={() => setFailed(true)}
        className="block h-auto w-full"
      />
      {pages > 1 ? (
        <figcaption className="border-t-2 border-ink px-3 py-1 font-mono text-sm text-muted">
          Стр. {page} / {pages}
        </figcaption>
      ) : null}
    </figure>
  );
}
