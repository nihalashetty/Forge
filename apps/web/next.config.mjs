/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
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
