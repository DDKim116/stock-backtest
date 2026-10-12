// 차트 보조지표 (일봉 종가 기준). 값이 없는 앞부분은 null.

export function sma(x: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(x.length).fill(null);
  let s = 0;
  for (let i = 0; i < x.length; i++) {
    s += x[i];
    if (i >= n) s -= x[i - n];
    if (i >= n - 1) out[i] = s / n;
  }
  return out;
}

export function bollinger(x: number[], n = 20, k = 2) {
  const mid = sma(x, n);
  const up: (number | null)[] = new Array(x.length).fill(null);
  const lo: (number | null)[] = new Array(x.length).fill(null);
  for (let i = n - 1; i < x.length; i++) {
    const m = mid[i] as number;
    let v = 0;
    for (let j = i - n + 1; j <= i; j++) v += (x[j] - m) ** 2;
    const sd = Math.sqrt(v / n);
    up[i] = m + k * sd;
    lo[i] = m - k * sd;
  }
  return { mid, up, lo };
}

// Wilder 방식 RSI (서버 조건식 rsi 와 같은 방식, 시작 부분만 조금 다를 수 있음)
export function rsi(x: number[], n = 14): (number | null)[] {
  const out: (number | null)[] = new Array(x.length).fill(null);
  let g = 0;
  let l = 0;
  for (let i = 1; i < x.length; i++) {
    const d = x[i] - x[i - 1];
    const up = Math.max(d, 0);
    const dn = Math.max(-d, 0);
    if (i <= n) {
      g += up / n;
      l += dn / n;
      if (i === n) out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
    } else {
      g = (g * (n - 1) + up) / n;
      l = (l * (n - 1) + dn) / n;
      out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
    }
  }
  return out;
}
