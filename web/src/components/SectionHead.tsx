// 카드 안 구역 제목: 매수 후보 = 색 테두리 이름표, 보유 종목 = 색을 채운 이름표 (장중 실시간 감시가 씀)
import type { ReactNode } from "react";

const INK = "#10131A";

export function SectionHead({ color, title, right, solid }: { color: string; title: string; right?: ReactNode; solid?: boolean }) {
  return (
    <div className="mb-1.5 flex items-center justify-between gap-2 text-xs">
      <span
        className="rounded-md px-2 py-0.5 font-semibold"
        style={solid ? { background: color, color: INK, border: `1.5px solid ${color}` } : { border: `1.5px solid ${color}` }}
      >
        {title}
      </span>
      {right && <span className="text-muted">{right}</span>}
    </div>
  );
}

