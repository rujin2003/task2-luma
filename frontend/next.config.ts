import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root: a stray package-lock.json above the repo otherwise wins.
  turbopack: { root: path.resolve(__dirname) },
};

export default nextConfig;
