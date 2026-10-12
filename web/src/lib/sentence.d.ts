export interface SentenceItem {
  op: "AND" | "OR" | null;
  src: string;
  exprs?: string[];
  desc?: string;
  note?: string | null;
  error?: string;
  unknown?: boolean;
}
export interface SentenceResult {
  normalized: string;
  items: SentenceItem[];
  signalLines: string[];
  oneLine: string;
  spec: string;
  rest: {
    buy: { type: "limit" | "next_open" | "close"; price?: string; valid?: number; desc: string } | null;
    exit: { target?: number; stop?: number; days?: number; basis?: string };
    universe: { markets?: string; start?: string; spac?: boolean; pref?: boolean };
    notes: string[];
  };
  restText: string;
}
export function convert(text: string): SentenceResult | null;
