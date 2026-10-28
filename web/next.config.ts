import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // самодостаточная сборка для Docker-образа
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    // загрузка документов идёт через server action: лимит по умолчанию (1 МБ) меньше лимита API (200 МБ на запрос)
    serverActions: { bodySizeLimit: "200mb" },
  },
};

export default nextConfig;
