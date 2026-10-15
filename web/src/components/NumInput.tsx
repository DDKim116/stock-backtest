"use client";

import { useLayoutEffect, useRef } from "react";

// 입력하는 동안에도 천 단위 콤마를 넣어 보여 주는 숫자 칸. value·onChange 는 콤마 없는 숫자 문자열.
function withCommas(raw: string): string {
  if (!raw) return "";
  const [int, dec] = raw.split(".");
  const i = int.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return dec !== undefined ? `${i}.${dec}` : i;
}

function clean(s: string, decimal: boolean): string {
  let v = s.replace(decimal ? /[^\d.]/g : /[^\d]/g, "");
  if (decimal) {
    const k = v.indexOf(".");
    if (k >= 0) v = v.slice(0, k + 1) + v.slice(k + 1).replace(/\./g, "");
  }
  return v;
}

export default function NumInput({
  value,
  onChange,
  decimal = false,
  className,
  placeholder,
  ariaLabel,
}: {
  value: string;
  onChange: (raw: string) => void;
  decimal?: boolean;
  className?: string;
  placeholder?: string;
  ariaLabel?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const caret = useRef<number | null>(null); // 커서 앞에 있던 숫자 개수
  const shown = withCommas(value);

  // 콤마가 생기거나 빠져도 커서가 같은 숫자 뒤에 머물게
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || caret.current == null || document.activeElement !== el) return;
    let n = caret.current;
    let pos = 0;
    while (pos < shown.length && n > 0) {
      if (shown[pos] !== ",") n--;
      pos++;
    }
    el.setSelectionRange(pos, pos);
    caret.current = null;
  }, [shown]);

  return (
    <input
      ref={ref}
      inputMode={decimal ? "decimal" : "numeric"}
      value={shown}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className={className}
      onChange={(e) => {
        const before = e.target.value.slice(0, e.target.selectionStart ?? e.target.value.length);
        caret.current = clean(before, decimal).length;
        onChange(clean(e.target.value, decimal));
      }}
    />
  );
}
