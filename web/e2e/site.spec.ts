import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:net";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { blankPng, type E2EState } from "./global-setup";

const state: E2EState = JSON.parse(readFileSync(path.join(__dirname, ".state.json"), "utf8"));
const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const ROOT = path.resolve(__dirname, "..", "..");

async function login(page: Page, password = state.password) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(state.email);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
}

async function fillAudit(page: Page, consent: boolean, name = "Тест Тестов", targets: string[] = []) {
  const form = page.locator("#audit form");
  await form.getByLabel("Имя *").fill(name);
  await form.getByLabel("Контакт *").fill("@e2e_test");
  await form.getByLabel("Документов в месяц *").selectOption({ index: 1 });
  const group = form.getByRole("group", { name: "Куда выгружать данные" });
  for (const t of targets) await group.getByLabel(t, { exact: true }).check();
  if (consent) await form.getByLabel(/Согласен на обработку персональных данных/).check();
  await form.locator('button[type="submit"]').click();
}

const LANDING_BLOCKS = [
  "problem",
  "systems",
  "rules",
  "how",
  "checks",
  "digital-pdf",
  "niches",
  "measured",
  "security",
  "rollout",
  "faq",
  "audit",
];

test("landing renders key sections", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("1С, CRM");
  for (const id of LANDING_BLOCKS) await expect(page.locator(`#${id}`)).toBeAttached();
  // ядро — куда уходят данные: все системы названы в блоке
  const systems = page.locator("#systems");
  for (const name of ["1С", "Битрикс24", "amoCRM", "Google Таблицы", "Excel", "Ваша система"]) {
    await expect(systems.getByRole("heading", { name, exact: true })).toBeVisible();
  }
  await expect(page.locator("#audit form")).toBeVisible();
  await expect(page.getByRole("group", { name: "Куда выгружать данные" })).toBeVisible();
});

test("landing has no horizontal scroll at 320/768/1440", async ({ page }) => {
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const url of ["/", "/audit", "/privacy", "/login"]) {
      await page.goto(url);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${url} at ${width}px`).toBeLessThanOrEqual(0);
    }
  }
});

test("audit form submits", async ({ page }) => {
  const name = `Тест ${Date.now()}`;
  await page.goto("/#audit");
  await fillAudit(page, true, name, ["1С", "Google Таблицы"]);
  await expect(page.getByRole("heading", { name: "Заявка принята" })).toBeVisible();
  // заявка действительно сохранена: её видно в CLI оператора
  const leads = execFileSync("docker", ["compose", "exec", "-T", "api", "docflow", "leads"], {
    cwd: ROOT,
    encoding: "utf8",
    timeout: 60_000,
  });
  const row = leads.split(/\r?\n/).find((line) => line.includes(name));
  expect(row).toBeTruthy();
  expect(row).toContain("1С, Google Таблицы");
});

test("audit form requires consent", async ({ page }) => {
  await page.goto("/#audit");
  await fillAudit(page, false);
  await expect(page.getByText("Без согласия на обработку персональных данных").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Заявка принята" })).toHaveCount(0);
});

test("anonymous app redirects to login", async ({ page }) => {
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible();
});

test("login wrong password shows error", async ({ page }) => {
  await login(page, "wrong-password-123");
  await expect(page.getByRole("alert").filter({ hasText: "Неверный email или пароль" })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("login → queue → review approve", async ({ page, context }) => {
  await login(page);
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByRole("heading", { name: "Очередь" })).toBeVisible();

  await page.goto(`/app/documents/${state.docId}`);
  await expect(page.getByText("Нужна проверка").first()).toBeVisible();
  const digital = page.getByRole("link", { name: "Цифровая версия (PDF)" });
  await expect(digital).toBeVisible();
  const digitalRes = await page.request.get((await digital.getAttribute("href")) as string);
  expect(digitalRes.status()).toBe(200);
  expect(digitalRes.headers()["content-type"]).toContain("application/pdf");
  expect((await digitalRes.body()).subarray(0, 4).toString()).toBe("%PDF");
  await page.getByRole("button", { name: "Подтвердить и отправить" }).click();
  // после подтверждения кабинет уводит со страницы документа (в очередь или к следующему)
  await expect(page).not.toHaveURL(new RegExp(`/app/documents/${state.docId}$`), { timeout: 15_000 });

  // итог проверяем в API, а не по тексту на странице
  const token = (await context.cookies()).find((c) => c.name === "df_session")?.value;
  expect(token).toBeTruthy();
  const res = await fetch(`${API}/v1/documents/${state.docId}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(res.status).toBe(200);
  expect(((await res.json()) as { status: string }).status).toBe("ok");
});

test("logout clears session", async ({ page, context }) => {
  await login(page);
  await expect(page).toHaveURL(/\/app$/);
  await page.getByRole("button", { name: "Выйти" }).click();
  await expect.poll(async () => (await context.cookies()).some((c) => c.name === "df_session")).toBe(false);
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login/);
});

test("revoked session redirects to login", async ({ page, context }) => {
  await login(page);
  await expect(page).toHaveURL(/\/app$/);
  const token = (await context.cookies()).find((c) => c.name === "df_session")?.value;
  expect(token).toBeTruthy();

  const res = await fetch(`${API}/v1/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
  expect(res.status).toBeLessThan(300);

  await page.reload();
  await expect(page).toHaveURL(/\/login\?expired=1/);
  await expect(page.getByText("Сессия истекла, войдите снова")).toBeVisible();
  expect((await context.cookies()).some((c) => c.name === "df_session")).toBe(false);
});

// --- правила, выгрузки, реестр Excel ---

const AUTH = { Authorization: `Bearer ${state.key}`, "Content-Type": "application/json" };

async function apiJson<T>(pathname: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${pathname}`, { ...init, headers: { ...AUTH, ...init.headers } });
  if (res.status >= 300) throw new Error(`${init.method ?? "GET"} ${pathname}: ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

/** Телеграм-подключение с чужим токеном: создание в Телеграм не ходит (в этом тесте подключение только лежит в списке). */
async function createTelegram(name: string): Promise<string> {
  const d = await apiJson<{ id: string }>("/v1/destinations", {
    method: "POST",
    body: JSON.stringify({
      kind: "telegram",
      name,
      config: { chat_id: "-100500" },
      secrets: { bot_token: "123456:e2e-dummy-token" },
    }),
  });
  return d.id;
}

test("rules: save and reload", async ({ page }) => {
  const dest = `Телеграм ${Date.now()}`;
  await createTelegram(dest);
  await login(page);
  await expect(page).toHaveURL(/\/app$/);
  await page.getByRole("link", { name: "Правила", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Правила", level: 1 })).toBeVisible();

  try {
    await page.getByRole("button", { name: "Добавить правило" }).click();
    await page.getByLabel("Название").fill("Крупные заказы");
    await page.getByLabel("Тип документа").selectOption("e2e_order");
    await page.getByRole("button", { name: "Добавить условие" }).click();
    await page.getByLabel("Поле", { exact: true }).selectOption("total");
    await page.getByLabel("Условие", { exact: true }).selectOption({ label: "больше" });
    await page.getByLabel("Значение", { exact: true }).fill("1000");
    await page.getByLabel(new RegExp(`^${dest}`)).check();
    await page.getByLabel("Требовать согласования").check();
    await page.getByRole("button", { name: "Сохранить правила" }).click();
    await expect(page.getByText("Правила сохранены.")).toBeVisible();

    await page.reload();
    await expect(page.getByLabel("Название")).toHaveValue("Крупные заказы");
    await expect(page.getByLabel("Тип документа")).toHaveValue("e2e_order");
    await expect(page.getByLabel("Поле", { exact: true })).toHaveValue("total");
    await expect(page.getByLabel("Значение", { exact: true })).toHaveValue("1000");
    await expect(page.getByLabel(new RegExp(`^${dest}`))).toBeChecked();
    await expect(page.getByLabel("Требовать согласования")).toBeChecked();

    // ошибка API показывается как есть: пустое условие не уходит в запрос
    await page.getByRole("button", { name: "Добавить условие" }).click();
    await page.getByRole("button", { name: "Сохранить правила" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "выберите поле" })).toBeVisible();
  } finally {
    await apiJson("/v1/routing-rules", { method: "PUT", body: JSON.stringify({ rules: [] }) });
  }
});

type ExportRow = { id: string; status: string; purpose: string; updated_at: string };

/** «Почтовый сервер» на хосте: здоровается кодом 554 — доставка падает сразу и без повторов, интернет не нужен. */
function refusingSmtp(): Promise<{ server: Server; port: number }> {
  return new Promise((resolve) => {
    const server = createServer((sock) => sock.on("error", () => {}).end("554 go away\r\n"));
    server.listen(0, "0.0.0.0", () => resolve({ server, port: (server.address() as { port: number }).port }));
  });
}

async function createRefusingMail(port: number): Promise<string> {
  const d = await apiJson<{ id: string }>("/v1/destinations", {
    method: "POST",
    body: JSON.stringify({
      kind: "email",
      name: `Почта ${Date.now()}`,
      config: {
        host: "host.docker.internal",
        port,
        security: "none",
        from_addr: "docflow@example.com",
        to_addrs: "boss@example.com",
      },
    }),
  });
  return d.id;
}

const listExports = async (docId: string) =>
  (await apiJson<{ items: ExportRow[] }>(`/v1/exports?document_id=${docId}`)).items;

test("exports block shows failed delivery with retry; excel registry downloads", async ({ page }) => {
  test.setTimeout(120_000);
  const { server, port } = await refusingSmtp();
  const dest = await createRefusingMail(port);
  try {
    await apiJson("/v1/routing-rules", {
      method: "PUT",
      body: JSON.stringify({
        rules: [{ name: "Все заказы", schema: "e2e_order", conditions: [], export_to: [], notify: [dest] }],
      }),
    });
    const form = new FormData();
    form.set("schema", "e2e_order");
    form.set("mode", "gray");
    form.append("files", new Blob([new Uint8Array(blankPng())], { type: "image/png" }), "o.png");
    const up = await fetch(`${API}/v1/documents`, {
      method: "POST",
      headers: { Authorization: AUTH.Authorization, "Idempotency-Key": `e2e-exp-${Date.now()}` },
      body: form,
    });
    expect(up.status).toBeLessThan(300);
    const docId = ((await up.json()) as { id: string }).id;
    await expect
      .poll(async () => (await apiJson<{ status: string }>(`/v1/documents/${docId}`)).status, { timeout: 60_000 })
      .toBe("needs_review");
    // «на проверку» ушло при needs_review; «новый документ» уйдёт после подтверждения
    await apiJson(`/v1/documents/${docId}/review`, { method: "POST", body: JSON.stringify({ decision: "approve" }) });
    await expect
      .poll(async () => (await listExports(docId)).filter((e) => e.status === "failed").length, { timeout: 60_000 })
      .toBe(2);

    await login(page);
    await expect(page).toHaveURL(/\/app$/);
    await page.goto(`/app/documents/${docId}`);
    const block = page.getByRole("region", { name: /Выгрузки/ });
    await expect(block).toBeVisible();
    const row = block.getByRole("row", { name: /Уведомление о новом/ });
    await expect(row.getByText("Ошибка", { exact: true })).toBeVisible();
    const retry = row.getByRole("button", { name: /Повторить выгрузку/ });
    await expect(retry).toBeVisible();

    // «Повторить» реально ставит выгрузку заново: у строки меняется updated_at
    const before = (await listExports(docId)).find((e) => e.purpose === "notify_new");
    expect(before).toBeTruthy();
    await retry.click();
    await expect
      .poll(async () => (await listExports(docId)).find((e) => e.purpose === "notify_new")?.updated_at, {
        timeout: 30_000,
      })
      .not.toBe(before?.updated_at);

    // реестр в Excel: через форму на очереди
    await page.goto("/app");
    await page.getByLabel("Тип документа").selectOption("e2e_order");
    await page.getByLabel("Статус документов").selectOption("all");
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Скачать реестр" }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^docflow-e2e_order-.*\.xlsx$/);

    const today = new Date().toISOString().slice(0, 10);
    const res = await page.request.get(`/app/files/excel?schema=e2e_order&from=${today}&to=${today}&status=all`);
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("spreadsheetml.sheet");
    expect(res.headers()["content-disposition"]).toContain(".xlsx");
    expect((await res.body()).subarray(0, 2).toString()).toBe("PK");

    // отказ показывается под формой, страница не уходит
    await page.getByLabel("С", { exact: true }).fill(today);
    await page.getByLabel("По", { exact: true }).fill("2020-01-01");
    await page.getByRole("button", { name: "Скачать реестр" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Конец периода раньше начала" })).toBeVisible();
    await expect(page).toHaveURL(/\/app$/);

    for (const q of ["schema=../x&from=2026-01-01&to=2026-01-02", `schema=e2e_order&from=2026-02-31&to=${today}`, `schema=e2e_order&from=${today}&to=2020-01-01`]) {
      expect((await page.request.get(`/app/files/excel?${q}`)).status(), q).toBe(400);
    }
  } finally {
    server.close();
    await apiJson("/v1/routing-rules", { method: "PUT", body: JSON.stringify({ rules: [] }) });
  }
});
