"use client";

import { useSyncExternalStore } from "react";

// 넓은 화면(폴드 펼침·태블릿·노트북)인지: globals.css 의 fold 기준(37.5rem)과 같다.
// 접었다 펴면 바로 바뀐다. 서버에서 그릴 때는 좁은 화면으로 본다.
const Q = "(min-width: 37.5rem)";

function subscribe(cb: () => void) {
  const m = window.matchMedia(Q);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
}

export function useWide(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(Q).matches,
    () => false,
  );
}
