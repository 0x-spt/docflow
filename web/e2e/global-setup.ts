import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { deflateSync } from "node:zlib";

const ROOT = path.resolve(__dirname, "..", "..");
const API = process.env.E2E_API_URL ?? "http://localhost:8000";
export const STATE_FILE = path.join(__dirname, ".state.json");

export type E2EState = { email: string; password: string; docId: string };

function cli(...args: string[]): string {
  return execFileSync("docker", ["compose", "exec", "-T", "api", "docflow", ...args], {
    cwd: ROOT,
    encoding: "utf8",
    timeout: 60_000,
  });
}

function grab(out: string, re: RegExp, what: string): string {
  const m = re.exec(out);
  if (!m) throw new Error(`не нашли ${what} в выводе docflow: ${out}`);
  return m[1];
}

/** Тестовая схема: итог не сходится с Σ количество × цена в заготовке tests/e2e/fake_responses.json. */
const ORDER_SCHEMA = {
  slug: "e2e_order",
  title: "Тестовый заказ-наряд",
  description: "Только для сквозных тестов сайта",
  fields: [
    { name: "number", type: "string", required: true },
    {
      name: "works",
      type: "table",
      columns: [
        { name: "name", type: "string" },
        { name: "qty", type: "number" },
        { name: "price", type: "number" },
      ],
    },
    { name: "total", type: "number" },
  ],
  rules: [{ kind: "table_product_sum", params: { table: "works", qty: "qty", price: "price", total: "total" } }],
};

function crc32(buf: Buffer): number {
  let c = ~0;
  for (const b of buf) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

/** Белый лист 600x800 в оттенках серого: без персональных данных. */
function blankPng(): Buffer {
  const w = 600;
  const h = 800;
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; // глубина
  ihdr[9] = 0; // оттенки серого
  const raw = Buffer.alloc((w + 1) * h, 255);
  for (let y = 0; y < h; y++) raw[y * (w + 1)] = 0; // фильтр строки: none
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

async function api(pathname: string, init: RequestInit, key: string): Promise<Response> {
  return fetch(`${API}${pathname}`, { ...init, headers: { ...init.headers, Authorization: `Bearer ${key}` } });
}

export default async function globalSetup(): Promise<void> {
  const stamp = Date.now();
  const password = `e2e-pass-${stamp}`;
  const email = `e2e-${stamp}@example.com`;

  const tenantOut = cli("create-tenant", `E2E ${stamp}`);
  const tenantId = grab(tenantOut, /\bid\s+([0-9a-f-]{36})/, "id клиента");
  const key = grab(tenantOut, /API-ключ:\s*(\S+)/, "API-ключ");
  cli("create-user", tenantId, email, "--role", "owner", "--password", password);

  const reg = await api(
    "/v1/schemas",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ORDER_SCHEMA) },
    key,
  );
  if (reg.status !== 201) throw new Error(`схема e2e_order не зарегистрирована: ${reg.status} ${await reg.text()}`);

  const form = new FormData();
  form.set("schema", "e2e_order");
  form.set("mode", "gray");
  form.append("files", new Blob([new Uint8Array(blankPng())], { type: "image/png" }), "order.png");
  const up = await api("/v1/documents", { method: "POST", headers: { "Idempotency-Key": `e2e-${stamp}` }, body: form }, key);
  if (up.status >= 300) throw new Error(`загрузка не удалась: ${up.status} ${await up.text()}`);
  const docId = ((await up.json()) as { id: string }).id;

  const deadline = Date.now() + 90_000;
  for (;;) {
    const doc = (await (await api(`/v1/documents/${docId}`, {}, key)).json()) as { status: string };
    if (doc.status === "needs_review") break;
    if (doc.status === "ok" || doc.status === "failed") throw new Error(`ожидали needs_review, получили ${doc.status}`);
    if (Date.now() > deadline) throw new Error(`документ не дошёл до needs_review: ${doc.status}`);
    await new Promise((r) => setTimeout(r, 1000));
  }

  mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  writeFileSync(STATE_FILE, JSON.stringify({ email, password, docId } satisfies E2EState));
}
