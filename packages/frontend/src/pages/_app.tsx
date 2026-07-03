import type { AppProps } from "next/app";
import { McpProvider } from "@/mcp/McpContext";
import "@/styles/globals.css";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <McpProvider>
      <Component {...pageProps} />
    </McpProvider>
  );
}
