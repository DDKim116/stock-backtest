// 휴대폰 위쪽 상태 표시줄 색(theme-color)을 지금 화면 밝기에 맞춘다.
// 앱 기본은 어둡게라서 기기 설정(밝게)만 보고 정하면 어두운 화면 위에 크림색 띠가 생길 수 있다.
// layout.tsx 의 첫 스크립트에도 같은 계산이 있다 (그리기 전에 적용).
export const THEME_COLOR = { light: "#faf6ee", dark: "#0e1117" } as const;

export function syncThemeColor(): void {
  try {
    const d = document.documentElement.dataset.theme;
    const light = d ? d === "light" : !window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((m) => {
      m.content = light ? THEME_COLOR.light : THEME_COLOR.dark;
    });
  } catch {}
}
