/** @type {import('next').NextConfig} */
const MCP_PORT = 8088;
const mcpPort = process.env.SPEC_MCP_PORT || String(MCP_PORT);
const isStatic = !!process.env.STATIC_EXPORT;

const nextConfig = {
  output: isStatic ? "export" : undefined,
  // Empty assetPrefix for static export — relative paths work everywhere
  assetPrefix: isStatic ? "" : undefined,

  // Bundle everything into fewer chunks
  experimental: {
    optimizePackageImports: ["mermaid"],
  },

  async rewrites() {
    return [
      {
        source: "/api/mcp",
        destination: `http://127.0.0.1:${mcpPort}/mcp`,
      },
    ];
  },
};

module.exports = nextConfig;
