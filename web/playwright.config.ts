import { defineConfig } from "@playwright/test";

// Сквозные тесты идут против живого стека: docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
});
