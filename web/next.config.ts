import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // самодостаточная сборка для Docker-образа
  output: "standalone",
  poweredByHeader: false,
};

export default nextConfig;
