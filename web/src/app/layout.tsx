import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Autobull",
  description: "규칙대로 사고 규칙대로 판다 · 국내·미국 주식 검증과 자동 매매",
  appleWebApp: { capable: true, title: "Autobull", statusBarStyle: "default" },
  icons: { icon: "/favicon.png", apple: "/apple-icon.png" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    // 실제 색은 아래 첫 스크립트와 lib/theme.ts 가 앱의 화면 밝기에 맞춰 바꾼다 (앱 기본은 어둡게)
    { media: "(prefers-color-scheme: light)", color: "#faf6ee" },
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
            __html: `try{var t=localStorage.getItem("sbt-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;else if(t==="system")delete document.documentElement.dataset.theme}catch(e){}try{var q=matchMedia("(prefers-color-scheme: dark)"),f=function(){var d=document.documentElement.dataset.theme,l=d?d==="light":!q.matches;document.querySelectorAll('meta[name="theme-color"]').forEach(function(m){m.content=l?"#faf6ee":"#0e1117"})};f();document.addEventListener("DOMContentLoaded",f);q.addEventListener("change",f)}catch(e){}`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
