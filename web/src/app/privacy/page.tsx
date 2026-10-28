import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PublicPage } from "@/components/landing/PublicPage";

export const metadata: Metadata = {
  title: "Политика обработки персональных данных — DocFlow",
  description: "Как DocFlow обрабатывает персональные данные из заявки на аудит.",
};

// Каркас политики по 152-ФЗ. Реквизиты и сроки — плейсхолдеры в квадратных скобках до заполнения оператором.
const SECTIONS: { title: string; body: ReactNode }[] = [
  {
    title: "Оператор",
    body: (
      <p>
        Оператор персональных данных — [ОПЕРАТОР ПД], ИНН [ИНН]. Связаться по вопросам обработки данных: [EMAIL],
        [ТЕЛЕФОН].
      </p>
    ),
  },
  {
    title: "Какие данные мы получаем",
    body: (
      <>
        <p>Из формы заявки на аудит:</p>
        <ul>
          <li>имя;</li>
          <li>название компании — если вы его указали;</li>
          <li>контакт для связи: телефон, Telegram или email;</li>
          <li>ниша, примерный объём документов, описание документов и сообщение — если вы их указали.</li>
        </ul>
        <p>
          Вместе с заявкой сохраняется IP-адрес, с которого она отправлена, — для защиты формы от злоупотреблений.
        </p>
      </>
    ),
  },
  {
    title: "Зачем",
    body: (
      <ul>
        <li>связаться с вами по заявке и договориться об аудите;</li>
        <li>подготовить предложение по внедрению DocFlow;</li>
        <li>защитить форму от автоматических и повторных отправок.</li>
      </ul>
    ),
  },
  {
    title: "Правовое основание",
    body: (
      <p>
        Ваше согласие на обработку персональных данных, которое вы даёте, отмечая галочку в форме заявки. Без
        согласия заявка не отправляется.
      </p>
    ),
  },
  {
    title: "Что мы делаем с данными и как долго храним",
    body: (
      <>
        <p>
          Сбор, запись, хранение, использование для связи с вами, удаление. Данные хранятся до достижения целей
          обработки или до отзыва согласия, но не дольше [СРОК ХРАНЕНИЯ].
        </p>
        <p>
          Третьим лицам данные не продаются и не передаются, кроме обработчиков, без которых работа сайта
          невозможна: [ПЕРЕЧЕНЬ ОБРАБОТЧИКОВ].
        </p>
      </>
    ),
  },
  {
    title: "Ваши права",
    body: (
      <>
        <p>Вы можете:</p>
        <ul>
          <li>узнать, какие ваши данные у нас есть и как они обрабатываются;</li>
          <li>потребовать уточнить, заблокировать или удалить данные;</li>
          <li>отозвать согласие на обработку.</li>
        </ul>
        <p>
          Для этого напишите на [EMAIL]. Если считаете, что ваши права нарушены, можно обратиться в Роскомнадзор или
          в суд.
        </p>
      </>
    ),
  },
];

export default function PrivacyPage() {
  return (
    <PublicPage>
      <article className="py-12 sm:py-20">
        <header className="max-w-4xl">
          <p className="font-mono text-base font-medium text-accent-text">[152-ФЗ]</p>
          <h1 className="mt-4 text-4xl font-semibold leading-[1] tracking-[-0.035em] text-balance sm:text-6xl">
            Политика обработки персональных данных
          </h1>
          <p className="mt-6 font-mono text-sm text-muted">Редакция от [ДАТА РЕДАКЦИИ]</p>
        </header>

        <ol className="mt-12 border-t-2 border-ink">
          {SECTIONS.map((s, i) => (
            <li key={s.title} className="grid gap-4 border-b-2 border-ink py-8 md:grid-cols-12">
              <h2 className="text-2xl font-semibold tracking-tight md:col-span-4">
                <span className="mr-3 font-mono text-base font-medium text-accent-text">
                  [{String(i + 1).padStart(2, "0")}]
                </span>
                {s.title}
              </h2>
              <div className="max-w-prose space-y-3 text-lg leading-relaxed md:col-span-7 md:col-start-6 [&_ul]:list-['—_'] [&_ul]:space-y-1 [&_ul]:pl-6">
                {s.body}
              </div>
            </li>
          ))}
        </ol>
      </article>
    </PublicPage>
  );
}
