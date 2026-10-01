export function pct(x: number | null | undefined, digits = 1, sign = false): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  const s = x.toFixed(digits);
  return (sign && x > 0 ? "+" : "") + s + "%";
}

export function num(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return Math.round(x).toLocaleString("ko-KR");
}

export function price(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return x >= 100 ? Math.round(x).toLocaleString("ko-KR") : x.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
}

export function retClass(x: number | null | undefined): string {
  if (x === null || x === undefined || x === 0) return "";
  return x > 0 ? "text-up" : "text-down";
}

/** "3, 5, 7" → [3,5,7] / "3" → 3 / "" → null */
export function parseNumList(s: string): number | number[] | null {
  const parts = s
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean)
    .map(Number);
  if (parts.length === 0) return null;
  if (parts.some((p) => Number.isNaN(p))) return NaN;
  return parts.length === 1 ? parts[0] : parts;
}

export function showNumList(v: number | number[] | null | undefined): string {
  if (v === null || v === undefined) return "";
  return Array.isArray(v) ? v.join(", ") : String(v);
}

/** 최상위 AND 로만 묶인 조건식을 줄 단위로 나눈다. OR 가 최상위에 있으면 나누지 않는다. */
export function splitTopLevelAnd(expr: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let cur = "";
  const tokens = expr.split(/(\s+(?:AND|and|그리고)\s+|[()])/);
  for (const tk of tokens) {
    if (tk === "(") depth++;
    if (tk === ")") depth--;
    if (depth === 0 && /^\s+(AND|and|그리고)\s+$/.test(tk)) {
      parts.push(cur.trim());
      cur = "";
    } else {
      cur += tk;
    }
  }
  parts.push(cur.trim());
  const clean = parts.filter(Boolean);
  if (clean.some((p) => /\s(OR|or|또는)\s/.test(stripParens(p)))) return [expr.trim()];
  return clean;
}

function stripParens(s: string): string {
  let depth = 0;
  let out = "";
  for (const ch of s) {
    if (ch === "(") depth++;
    if (depth === 0) out += ch;
    if (ch === ")") depth--;
  }
  return out;
}

export function joinAnd(rows: string[]): string {
  return rows
    .map((r) => r.trim())
    .filter(Boolean)
    .map((r) => (/\s(OR|or|또는)\s/.test(stripParens(r)) ? `(${r})` : r))
    .join(" AND ");
}
