import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "주식 검증기",
    short_name: "주식검증",
    description: "국내·미국 주식 일봉 백테스트",
    start_url: "/",
    display: "standalone",
    background_color: "#f6f6f4",
    theme_color: "#16233a",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
