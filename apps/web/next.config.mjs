/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a minimal self-contained server bundle (.next/standalone) for a lean prod image.
  // Gated on an env var: the standalone copy step uses symlinks that fail on Windows dev
  // (EPERM) - the Docker build sets NEXT_OUTPUT=standalone; local `pnpm build` stays plain.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  env: {
    // Browser-facing base for DIRECT client calls (e.g. split-origin SSE). Leave EMPTY for
    // container / single-origin deploys so the browser uses the same-origin /api/forge proxy.
    // NOT derived from FORGE_API_URL: that is the container-internal rewrite host (e.g.
    // http://api:8000) which the browser cannot resolve. Set only for a split-origin deploy.
    NEXT_PUBLIC_FORGE_API_URL: process.env.NEXT_PUBLIC_FORGE_API_URL || "",
  },
  async rewrites() {
    // Server-side proxy target, baked at BUILD time for standalone output (the destination is
    // frozen into the routes manifest, so the runtime env cannot change it). In compose this is
    // the container-internal API address, passed via the Dockerfile ARG (build.args
    // FORGE_API_URL=http://api:8000). Host `pnpm dev` leaves FORGE_API_URL unset -> 127.0.0.1:8000.
    const api = process.env.FORGE_API_URL || "http://127.0.0.1:8000";
    return [{ source: "/api/forge/:path*", destination: `${api}/:path*` }];
  },
};
export default nextConfig;
