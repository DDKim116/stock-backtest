"use client";

const KEY_URL = "sbt.apiUrl";
const KEY_TOKEN = "sbt.token";

function read(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function write(key: string, value: string) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* 저장소를 쓸 수 없는 환경 */
  }
}

/** 분석 서버 주소. 설정에서 바꾼 값 → 빌드 시 지정값 → 같은 주소 순. */
export function apiUrl(): string {
  return (read(KEY_URL) || process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
}

export function setApiUrl(url: string) {
  write(KEY_URL, url.trim());
}

export function setToken(token: string) {
  write(KEY_TOKEN, token);
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const headers: Record<string, string> = {};
  const token = read(KEY_TOKEN);
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (init?.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(apiUrl() + path, {
      method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
      headers,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError("분석 서버에 연결할 수 없습니다. 설정에서 서버 주소를 확인하세요.", 0);
  }
  if (!res.ok) {
    let msg = `오류 ${res.status}`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg: string }) => d.msg).join("\n");
    } catch {
      /* 본문 없음 */
    }
    throw new ApiError(msg, res.status);
  }
  return res.json() as Promise<T>;
}
