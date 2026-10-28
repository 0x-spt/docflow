import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import type { E2EState } from "./global-setup";

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
