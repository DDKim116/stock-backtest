import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "규칙대로",
    short_name: "규칙대로",
    description: "규칙대로 사고 규칙대로 판다 · 국내·미국 주식 검증과 자동 매매",
    start_url: "/",
    display: "standalone",
    background_color: "#0e1117",
    theme_color: "#16233a",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
