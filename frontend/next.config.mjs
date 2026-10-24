// The browser talks to /api/*; Next proxies it to the FastAPI service. This keeps one origin
// (no CORS) and lets the proxy hold the API key in production (see hardening).
const apiInternal = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  compress: false, // gzip buffers server-sent events
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiInternal}/:path*` }];
  },
};

export default nextConfig;
