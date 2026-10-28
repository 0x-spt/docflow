import type { NextConfig } from "next";

// защита от встраивания в чужие фреймы (кликджекинг), утечки адреса и подмены типа — на всех адресах
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Content-Type-Options", value: "nosniff" },
];

const nextConfig: NextConfig = {
  // самодостаточная сборка для Docker-образа
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    // загрузка документов идёт через server action: лимит по умолчанию (1 МБ) меньше лимита API (200 МБ на запрос)
    serverActions: { bodySizeLimit: "200mb" },
  },
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
