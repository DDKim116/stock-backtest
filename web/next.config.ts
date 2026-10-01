import type { NextConfig } from "next";

// 정적 파일로 내보내서 Vercel 이나 분석 서버(FastAPI) 어디서든 그대로 서비스한다.
const nextConfig: NextConfig = {
  output: "export",
};

export default nextConfig;
