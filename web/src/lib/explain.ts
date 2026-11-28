"use client";

import { useSyncExternalStore } from "react";

// 앱 전체 '설명: 간단히 / 자세히'. 보는 사람 기기에만 기억한다 (기본: 간단히).
// 정적 내보내기라 서버에서 그린 화면은 항상 '간단히' → 브라우저에서 저장값으로 바꾼다 (화면 깨짐 없이).
export type ExplainMode = "simple" | "detail";

const KEY = "sbt-explain";
const listeners = new Set<() => void>();
let current: ExplainMode | null = null;

function read(): ExplainMode {
  try {
    return localStorage.getItem(KEY) === "detail" ? "detail" : "simple";
  } catch {
    return "simple";
  }
}

function snapshot(): ExplainMode {
  if (current === null) current = read();
  return current;
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      current = read();
      fn();
    }
  };
  window.addEventListener("storage", onStorage); // 다른 창에서 바꾼 것도 따라간다
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", onStorage);
  };
}

export function setExplainMode(m: ExplainMode) {
  current = m;
  try {
    localStorage.setItem(KEY, m);
  } catch {
    /* 저장소를 쓸 수 없는 환경: 이번 화면에서만 */
  }
  listeners.forEach((f) => f());
}

export function useExplain() {
  const mode = useSyncExternalStore(subscribe, snapshot, () => "simple" as ExplainMode);
  return { mode, detail: mode === "detail", setMode: setExplainMode };
}
