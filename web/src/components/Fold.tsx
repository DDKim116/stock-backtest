"use client";

import { type ReactNode, useState } from "react";

const read = (key: string, fallback: boolean): boolean => {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : v === "1";
  } catch {
    return fallback;
  }
};

// 접고 펴는 상자: 제목 줄을 누르면 열리고 닫힌다. 마지막 상태는 이 기기에 기억한다
export default function Fold({
  id,
  title,
  sub,
  defaultOpen = false,
  children,
}: {
  id: string;
  title: string;
  sub?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const key = `sbt.fold.${id}`;
  const [open, setOpen] = useState(() => read(key, defaultOpen));
  const toggle = () => {
    setOpen((o) => {
      try {
        localStorage.setItem(key, o ? "0" : "1");
      } catch {
        /* 저장소를 쓸 수 없는 환경 */
      }
      return !o;
    });
  };
  return (
    <section className="rounded-xl border border-border bg-card">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
      >
        <span className="min-w-0">
          <span className="font-semibold">{title}</span>
          {sub && <span className="ml-2 text-xs text-muted">{sub}</span>}
        </span>
        <svg
          aria-hidden="true"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && <div className="space-y-3 border-t border-border p-3">{children}</div>}
    </section>
  );
}
