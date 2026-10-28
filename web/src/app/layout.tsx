import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "주식 검증기",
  description: "국내·미국 주식 일봉 백테스트",
  appleWebApp: { capable: true, title: "주식 검증기", statusBarStyle: "default" },
  icons: { icon: "/icon-192.png", apple: "/icon-192.png" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#0e1117" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1117" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* 화면 밝기: 기본은 어둡게(시안 A). 설정에서 고른 값이 있으면 그리기 전에 적용 */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("sbt-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;else if(t==="system")delete document.documentElement.dataset.theme}catch(e){}`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
