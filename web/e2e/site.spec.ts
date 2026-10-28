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

async function fillAudit(page: Page, consent: boolean, name = "Тест Тестов") {
  const form = page.locator("#audit form");
  await form.getByLabel("Имя *").fill(name);
  await form.getByLabel("Контакт *").fill("@e2e_test");
  await form.getByLabel("Документов в месяц *").selectOption({ index: 1 });
  if (consent) await form.getByRole("checkbox").check();
  await form.locator('button[type="submit"]').click();
}

test("landing renders key sections", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  for (const id of ["problem", "how", "checks", "measured", "niches", "security", "integrations", "rollout", "faq", "audit"]) {
    await expect(page.locator(`#${id}`)).toBeAttached();
  }
  await expect(page.locator("#audit form")).toBeVisible();
});

test("audit form submits", async ({ page }) => {
  const name = `Тест ${Date.now()}`;
  await page.goto("/#audit");
  await fillAudit(page, true, name);
  await expect(page.getByRole("heading", { name: "Заявка принята" })).toBeVisible();
  // заявка действительно сохранена: её видно в CLI оператора
  const leads = execFileSync("docker", ["compose", "exec", "-T", "api", "docflow", "leads"], {
    cwd: ROOT,
    encoding: "utf8",
    timeout: 60_000,
  });
  expect(leads).toContain(name);
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
