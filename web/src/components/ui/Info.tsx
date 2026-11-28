"use client";

import { createContext, type ReactNode, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { type ExplainMode, useExplain } from "@/lib/explain";

// 설명 묶음: 제목 옆 ⓘ 버튼(InfoButton)과 그 항목의 설명(Explain·Swap)이 같이 열리고 닫힌다.
// '자세히'면 처음부터 열려 있고, '간단히'면 ⓘ 를 눌렀을 때만 보인다. 앱 전체 설정을 바꾸면 그쪽을 따른다.
// manual 묶음은 앱 설정과 상관없이 ⓘ 를 눌러야만 열린다 (예: 숫자 칸 격자가 펼쳐져 모양이 바뀌면 안 될 때).
type Group = {
  open: boolean;
  toggle: () => void;
  id: string;
  ids: string[]; // 이 묶음이 여닫는 설명 칸들의 id (버튼의 aria-controls)
  register: (id: string) => () => void;
  byUser: boolean; // 지금 상태가 직접 누른 결과인지 (아니면 앱 설정을 따르는 중)
};
const Ctx = createContext<Group | null>(null);

function useGroupState(manual = false): Group {
  const { mode, detail } = useExplain();
  const id = useId();
  // 직접 누른 값은 그때의 앱 설정에서만 유효 (설정을 바꾸면 다시 설정을 따른다). manual 이면 늘 직접 누른 값
  const [local, setLocal] = useState<{ mode: ExplainMode; open: boolean } | null>(null);
  const byUser = manual || (!!local && local.mode === mode);
  const open = manual ? !!local?.open : byUser ? !!local?.open : detail;
  const reg = useRef<Set<string>>(new Set());
  const [ids, setIds] = useState<string[]>([]);
  const register = useCallback((rid: string) => {
    reg.current.add(rid);
    setIds([...reg.current]);
    return () => {
      reg.current.delete(rid);
      setIds([...reg.current]);
    };
  }, []);
  return { open, id, ids, register, byUser, toggle: () => setLocal({ mode, open: !open }) };
}

export function InfoGroup({ children, manual = false }: { children: ReactNode; manual?: boolean }) {
  return <Ctx.Provider value={useGroupState(manual)}>{children}</Ctx.Provider>;
}

// 묶음 안의 설명 칸 하나: 고유 id 를 묶음에 알린다 (버튼의 aria-controls 에 들어간다)
function useRegion(g: Group | null): string {
  const rid = useId();
  const register = g?.register;
  useEffect(() => (register ? register(rid) : undefined), [register, rid]);
  return rid;
}

// 묶음이 열려 있는지와 그 칸의 id (요약/전체를 바꿔 보여 줄 때). 묶음 밖이면 앱 설정을 따른다
export function useInfoRegion(): { open: boolean; id: string } {
  const g = useContext(Ctx);
  const { detail } = useExplain();
  const id = useRegion(g);
  return { open: g ? g.open : detail, id };
}

// 작은 원형 i (눈에 보이는 원은 작게)
function Dot({ open }: { open: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`flex h-[18px] w-[18px] items-center justify-center rounded-full border text-[11px] font-semibold leading-none ${
        open ? "border-accent bg-accent text-on-accent" : "border-faint text-faint"
      }`}
    >
      i
    </span>
  );
}

// 제목 옆 ⓘ 버튼 (누르는 영역 36px)
export function InfoButton({ label, className = "" }: { label: string; className?: string }) {
  const g = useContext(Ctx);
  if (!g) return null;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation(); // 접는 상자 제목 줄 안에 있어도 상자는 그대로
        g.toggle();
      }}
      aria-label={`${label} 설명 ${g.open ? "접기" : "보기"}`}
      aria-expanded={g.open}
      aria-controls={g.open && g.ids.length ? g.ids.join(" ") : undefined}
      className={`-my-2 inline-flex h-9 w-9 shrink-0 items-center justify-center align-middle ${className}`}
    >
      <Dot open={g.open} />
    </button>
  );
}

// 덧붙이는 설명: 묶음(InfoGroup) 안이면 그 ⓘ 를 따르고, 묶음 밖이면 스스로 작은 'ⓘ 이름' 버튼을 단다.
export function Explain({ children, className = "", label }: { children: ReactNode; className?: string; label: string }) {
  const g = useContext(Ctx);
  return g ? (
    <GroupExplain g={g} className={className}>
      {children}
    </GroupExplain>
  ) : (
    <SoloExplain label={label} className={className}>
      {children}
    </SoloExplain>
  );
}

function GroupExplain({ g, children, className }: { g: Group; children: ReactNode; className: string }) {
  const rid = useRegion(g);
  return g.open ? (
    <div id={rid} className={className}>
      {children}
    </div>
  ) : null;
}

function SoloExplain({ children, className, label }: { children: ReactNode; className: string; label: string }) {
  const g = useGroupState();
  const aria = { "aria-label": `${label} 설명 ${g.open ? "접기" : "보기"}`, "aria-expanded": g.open };
  // '자세히'라서 열려 있으면 원래 화면처럼 설명만 (버튼 없음 → 길이가 원래와 같다).
  // '간단히'에서 ⓘ 로 연 경우엔 설명 오른쪽 위에 작은 ⓘ(접기)를 띄워 둔다.
  if (g.open && !g.byUser)
    return (
      <div id={g.id} className={className}>
        {children}
      </div>
    );
  return g.open ? (
    <div className="flow-root">
      <FloatDot g={g} {...aria} />
      <div id={g.id} className={className}>
        {children}
      </div>
    </div>
  ) : (
    <div className="flex">
      <button type="button" onClick={g.toggle} {...aria} className="-my-2 inline-flex min-h-9 items-center gap-1.5 text-xs text-faint">
        <Dot open={false} />
        {label}
      </button>
    </div>
  );
}

// 글 오른쪽 위에 떠 있는 ⓘ (누르는 영역 36px, 글은 첫 줄만 비켜 간다)
function FloatDot({ g, ...aria }: { g: Group; "aria-label": string; "aria-expanded": boolean }) {
  return (
    <button
      type="button"
      onClick={g.toggle}
      {...aria}
      aria-controls={g.id}
      className="float-right -my-2 -mr-2 ml-1 inline-flex h-9 w-9 items-center justify-center"
    >
      <Dot open={g.open} />
    </button>
  );
}

// 바꿔 끼우는 설명: 닫혀 있으면 요약(short)만, 열리면(ⓘ 또는 '자세히') 원문(children)만 — 둘이 겹쳐 길어지지 않게.
// 묶음 밖이면 오른쪽에 작은 ⓘ 버튼을 스스로 단다.
export function Swap({ short, children, label, className = "" }: { short: ReactNode; children: ReactNode; label: string; className?: string }) {
  const g = useContext(Ctx);
  return g ? (
    <GroupSwap g={g} short={short} className={className}>
      {children}
    </GroupSwap>
  ) : (
    <SoloSwap short={short} label={label} className={className}>
      {children}
    </SoloSwap>
  );
}

function GroupSwap({ g, short, children, className }: { g: Group; short: ReactNode; children: ReactNode; className: string }) {
  const rid = useRegion(g);
  return (
    <div id={rid} className={className}>
      {g.open ? children : short}
    </div>
  );
}

function SoloSwap({ short, children, label, className }: { short: ReactNode; children: ReactNode; label: string; className: string }) {
  const g = useGroupState();
  // '자세히'라서 열려 있으면 원문만 (원래 화면과 같은 길이)
  if (g.open && !g.byUser)
    return (
      <div id={g.id} className={className}>
        {children}
      </div>
    );
  return (
    <div className={`flow-root ${className}`}>
      <FloatDot g={g} aria-label={`${label} 설명 ${g.open ? "접기" : "보기"}`} aria-expanded={g.open} />
      <div id={g.id}>{g.open ? children : short}</div>
    </div>
  );
}

// 앱 전체 '설명: 간단히 | 자세히' 스위치 (머리글·설정에서 같이 쓴다) — 눌린 쪽이 aria-pressed
export function ExplainToggle({ size = "sm" }: { size?: "sm" | "md" }) {
  const { mode, setMode } = useExplain();
  const pad = size === "md" ? "flex-1 py-2 text-sm" : "px-2 py-1 text-xs";
  return (
    <div className={`flex gap-0.5 rounded-lg border border-border ${size === "md" ? "w-full p-1" : "shrink-0 p-px"}`} role="group" aria-label="설명 보기">
      {(
        [
          ["simple", "간단히"],
          ["detail", "자세히"],
        ] as [ExplainMode, string][]
      ).map(([k, label]) => (
        <button
          key={k}
          type="button"
          aria-pressed={mode === k}
          onClick={() => setMode(k)}
          className={`whitespace-nowrap rounded-md ${pad} ${mode === k ? "bg-accent font-semibold text-on-accent" : "text-muted"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
