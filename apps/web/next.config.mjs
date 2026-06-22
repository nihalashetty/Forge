/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a minimal self-contained server bundle (.next/standalone) for a lean prod image.
  // Gated on an env var: the standalone copy step uses symlinks that fail on Windows dev
  // (EPERM) - the Docker build sets NEXT_OUTPUT=standalone; local `pnpm build` stays plain.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  env: {
    NEXT_PUBLIC_FORGE_API_URL: process.env.FORGE_API_URL || "",
  },
  async rewrites() {
    // Proxy API calls to the Forge backend so the app and API share an origin in dev.
    const api = process.env.FORGE_API_URL || "http://127.0.0.1:8000";
    return [{ source: "/api/forge/:path*", destination: `${api}/:path*` }];
  },
};
export default nextConfig;
