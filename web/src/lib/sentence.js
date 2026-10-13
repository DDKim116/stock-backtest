// 문장 → 조건식 규칙 변환 (AI 미사용). 도구 페이지(조건식 변환기)와 같은 코드.
/* 문장 → 조건식 변환 (규칙 기반, AI 미사용) */
const Conv = (() => {
  const KNUM = { 한: 1, 두: 2, 세: 3, 네: 4, 다섯: 5, 여섯: 6, 일곱: 7, 여덟: 8, 아홉: 9, 열: 10 };
  const fmt = (x) => String(Math.round(x * 10000) / 10000);

  function norm(t) {
    return t
      .replace(/\s*\n+\s*/g, ", ")
      .replace(/％/g, "%")
      .replace(/\s*(퍼센트|프로)/g, "%")
      .replace(/(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*배/g, (m, k) => KNUM[k] + "배")
      .replace(/(\d),(?=\d{3})/g, "$1")
      .replace(/(\d+(?:\.\d+)?)\s*천\s*(억|만)/g, (m, n, u) => +n * 1000 + u)
      .replace(/(\d+(?:\.\d+)?)\s*백\s*(억|만)/g, (m, n, u) => +n * 100 + u)
      .replace(/[“”"']/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  const UP = /(상승|오르|올라|올랐|오른|증가|늘|급등|뛰|뛴|플러스)/;
  const DOWN = /(하락|내리|내린|내려|떨어|감소|줄|급락|빠지|빠진|빠졌|마이너스)/;
  function cmpOf(c, dflt = ">=") {
    if (/이상/.test(c)) return ">=";
    if (/이하/.test(c)) return "<=";
    if (/(초과|넘)/.test(c)) return ">";
    if (/(미만|못 미치|안 되)/.test(c)) return "<";
    return dflt;
  }
  const flip = { ">=": "<=", ">": "<", "<=": ">=", "<": ">" };
  const OPWORD = { ">=": "이상", ">": "초과", "<=": "이하", "<": "미만" };
  function pctOf(c) {
    const m = c.match(/(\d+(?:\.\d+)?)\s*%/);
    return m ? +m[1] : null;
  }
  function daysOf(m) {
    const n = +m[1], u = m[2];
    if (/주/.test(u)) return n === 52 ? 250 : n * 5;
    if (/(개월|달)/.test(u)) return n * 21;
    if (/년/.test(u)) return n * 250;
    return n;
  }
  const PERIOD = /(\d+)\s*(거래일|일|주|개월|달|년)/;
  function periodOf(c) {
    if (/한\s*달/.test(c)) return 21;
    const m = c.match(PERIOD);
    return m ? daysOf(m) : null;
  }
  function amtOf(s) {
    const m = s.match(/(\d+(?:\.\d+)?)\s*(조|억|만)?/);
    return m ? m[1] + (m[2] || "") : null;
  }
  // "A 이상 B 이하", "A~B" 범위
  function rangeOf(c, unitRe) {
    const re = new RegExp(
      `(\\d+(?:\\.\\d+)?\\s*[조억만]?)\\s*${unitRe}?\\s*(?:이상|부터|에서|~|-)\\s*(\\d+(?:\\.\\d+)?\\s*[조억만]?)\\s*${unitRe}?\\s*(이하|까지|미만|사이)?`,
    );
    const m = c.match(re);
    if (!m) return null;
    return [m[1].replace(/\s/g, ""), m[2].replace(/\s/g, ""), m[3] === "미만" ? "<" : "<="];
  }

  const UNSUPPORTED = /(외국인|기관|개인\s*순|수급|공매도|PER|PBR|ROE|실적|영업이익|매출|배당|뉴스|공시|테마|호가|분봉|체결강도)/i;

  // ---------------------------------------------------------------- 신호 규칙
  // 각 규칙: (c) => null | {exprs:[...], desc, note?, start?}
  const RULES = [
    function unsupported(c) {
      const m = c.match(UNSUPPORTED);
      if (m) return { error: `'${m[1]}' 데이터는 아직 없어서 조건으로 쓸 수 없습니다 (일봉 가격·거래량만 있음).` };
      return null;
    },
    function volume(c) { return flow(c, "거래량"); },
    function value(c) { return flow(c, "거래대금"); },
    function mktcap(c) {
      if (!/(시가총액|시총)/.test(c)) return null;
      const r = rangeOf(c, "원");
      if (r) return { exprs: [`시가총액 >= ${r[0]}`, `시가총액 ${r[2]} ${r[1]}`], desc: `시가총액 ${r[0]} ~ ${r[1]}` };
      const a = amtOf(c.slice(c.search(/(시가총액|시총)/)));
      if (!a) return null;
      const op = /(작|적|소형)/.test(c) && !/이상/.test(c) ? "<" : cmpOf(c);
      return { exprs: [`시가총액 ${op} ${a}`], desc: `시가총액 ${a}원 ${OPWORD[op]}`, note: "미국 데이터에는 시가총액 값이 없습니다." };
    },
    function rsi(c) {
      if (!/(rsi|과매도|과매수)/i.test(c)) return null;
      let period = 14;
      const pm = c.match(/rsi\s*\(\s*(\d+)\s*\)|rsi(\d+)|(\d+)\s*일\s*rsi/i);
      let rest = c;
      if (pm) { period = +(pm[1] || pm[2] || pm[3]); rest = c.replace(pm[0], " "); }
      const vm = rest.match(/(\d+(?:\.\d+)?)/);
      const f = period === 14 ? "rsi(14)" : `rsi(${period})`;
      if (vm) {
        const op = cmpOf(rest, /과매수/.test(c) ? ">=" : "<=");
        return { exprs: [`${f} ${op} ${vm[1]}`], desc: `RSI(${period}) ${vm[1]} ${OPWORD[op]}` };
      }
      if (/과매수/.test(c)) return { exprs: [`${f} > 70`], desc: `RSI(${period}) 70 초과 (과매수)` };
      return { exprs: [`${f} < 30`], desc: `RSI(${period}) 30 미만 (과매도)` };
    },
    function cross(c) {
      const g = /골든\s*크로스|골크/.test(c), d = /데드\s*크로스/.test(c);
      if (!g && !d) return null;
      const ns = [...c.matchAll(/(\d+)\s*(일|주)?/g)].map((m) => (/주/.test(m[2] || "") ? +m[1] * 5 : +m[1]));
      const [a, b] = ns.length >= 2 ? ns : [5, 20];
      const f = g ? "상향돌파" : "하향돌파";
      return {
        exprs: [`${f}(이평(종가, ${a}), 이평(종가, ${b}))`],
        desc: `${a}일선이 ${b}일선을 ${g ? "아래에서 위로" : "위에서 아래로"} 뚫음`,
        note: ns.length >= 2 ? null : "이평선 기간이 없어 5일선·20일선으로 가정했습니다.",
      };
    },
    function align(c) {
      const up = /정배열/.test(c), dn = /역배열/.test(c);
      if (!up && !dn) return null;
      const ns = [...c.matchAll(/(\d+)\s*일/g)].map((m) => +m[1]);
      const [a, b, d] = ns.length >= 3 ? ns : [5, 20, 60];
      const op = up ? ">" : "<";
      return {
        exprs: [`이평(종가, ${a}) ${op} 이평(종가, ${b})`, `이평(종가, ${b}) ${op} 이평(종가, ${d})`],
        desc: `${a}·${b}·${d}일선 ${up ? "정배열" : "역배열"}`,
      };
    },
    function newHigh(c) {
      const hi = /신고가|최고가\s*(경신|돌파|갱신)/.test(c), lo = /신저가|최저가\s*(경신|갱신|이탈)/.test(c);
      if (!hi && !lo) return null;
      let n = periodOf(c), note = null;
      if (!n) { n = 250; note = "기간이 없어 52주(250거래일)로 가정했습니다."; }
      const p = pctOf(c);
      if (hi && p != null && /(근접|근처|이내|가까)/.test(c))
        return { exprs: [`종가 >= 최고(고가, ${n}) * ${fmt(1 - p / 100)}`], desc: `${n}일 최고가에서 ${p}% 이내`, note };
      if (hi) return { exprs: [`종가 > 이전(최고(고가, ${n}), 1)`], desc: `종가가 직전 ${n}일 최고가를 넘음`, note };
      return { exprs: [`종가 < 이전(최저(저가, ${n}), 1)`], desc: `종가가 직전 ${n}일 최저가 아래`, note };
    },
    function limit(c) {
      const u = /상한가/.test(c), d = /하한가/.test(c);
      if (!u && !d) return null;
      return {
        exprs: [u ? "등락률 >= 29.5" : "등락률 <= -29.5"],
        desc: u ? "상한가 (등락률 29.5% 이상)" : "하한가 (등락률 -29.5% 이하)",
        note: "가격제한폭이 30%가 된 2015-06-15부터로 기간을 맞췄습니다.",
        start: "2015-06-15",
      };
    },
    function streak(c) {
      const m = c.match(/(\d+)\s*(?:거래)?일\s*(?:연속|째|동안 연속|연속으로)\s*(상승|양봉|하락|음봉|오르|내리)/) ||
        c.match(/연속\s*(\d+)\s*(?:거래)?일\s*(?:간|동안)?\s*(상승|양봉|하락|음봉|오르|내리)/);
      if (!m) return null;
      const n = +m[1], k = m[2];
      const inner = /양봉/.test(k) ? "종가 > 시가" : /음봉/.test(k) ? "종가 < 시가" : /(하락|내리)/.test(k) ? "등락률 < 0" : "등락률 > 0";
      const word = /양봉/.test(k) ? "양봉" : /음봉/.test(k) ? "음봉" : /(하락|내리)/.test(k) ? "하락" : "상승";
      return { exprs: [`횟수(${inner}, ${n}) == ${n}`], desc: `${n}일 연속 ${word}` };
    },
    function gap(c) {
      if (!/(갭|시가가?\s*전일|시초가)/.test(c)) return null;
      const p = pctOf(c);
      const dn = /(갭\s*하락|갭\s*다운|하락\s*출발|낮게)/.test(c);
      if (p != null) {
        return dn
          ? { exprs: [`시가 <= 종가(1) * ${fmt(1 - p / 100)}`], desc: `시가가 전일 종가보다 ${p}% 이상 낮게 시작` }
          : { exprs: [`시가 >= 종가(1) * ${fmt(1 + p / 100)}`], desc: `시가가 전일 종가보다 ${p}% 이상 높게 시작` };
      }
      return dn
        ? { exprs: ["시가 < 저가(1)"], desc: "전일 저가보다 낮게 시작 (갭하락)" }
        : { exprs: ["시가 > 고가(1)"], desc: "전일 고가보다 높게 시작 (갭상승)" };
    },
    function fromHigh(c) {
      const m = c.match(/(?:(\d+)\s*(거래일|일|주|개월|달|년)\s*)?(최고가|고점|최고|저점|최저가|최저)\s*(?:대비|에서|보다)/);
      if (!m) return null;
      const p = pctOf(c);
      if (p == null) return null;
      let n = m[1] ? daysOf([null, m[1], m[2]]) : null, note = null;
      if (!n) { n = 60; note = "기간이 없어 최근 60거래일 고점/저점으로 가정했습니다."; }
      if (/(고점|최고)/.test(m[3]))
        return { exprs: [`종가 <= 최고(고가, ${n}) * ${fmt(1 - p / 100)}`], desc: `최근 ${n}일 최고가보다 ${p}% 이상 낮음`, note };
      return { exprs: [`종가 >= 최저(저가, ${n}) * ${fmt(1 + p / 100)}`], desc: `최근 ${n}일 최저가보다 ${p}% 이상 높음`, note };
    },
    function dayHigh(c) {
      if (!/(당일\s*)?고가\s*(대비|에서|보다)/.test(c)) return null;
      const p = pctOf(c);
      if (p == null) return null;
      return { exprs: [`종가 <= 고가 * ${fmt(1 - p / 100)}`], desc: `종가가 그날 고가보다 ${p}% 이상 밀림 (윗꼬리)` };
    },
    function periodReturn(c) {
      const m = c.match(/(\d+)\s*(거래일|일|주|개월|달|년)\s*(?:동안|간|사이|만에|전보다|전\s*대비|새)/);
      const p = pctOf(c);
      if (!m || p == null || /(선|이평|평균)/.test(c)) return null;
      const n = daysOf(m), op = cmpOf(c);
      if (DOWN.test(c)) return { exprs: [`수익률(${n}) ${flip[op]} -${p}`], desc: `최근 ${n}일 동안 ${p}% ${OPWORD[op]} 하락` };
      return { exprs: [`수익률(${n}) ${op} ${p}`], desc: `최근 ${n}일 동안 ${p}% ${OPWORD[op]} 상승` };
    },
    function movingAvg(c) {
      const ms = [...c.matchAll(/(\d+)\s*(일|주)?\s*(?:이동\s*평균\s*선?|이평\s*선?|선)/g)];
      if (!ms.length) return null;
      const ns = ms.map((m) => (m[2] === "주" ? +m[1] * 5 : +m[1]));
      const ma = (n) => `이평(종가, ${n})`;
      const brk = /(돌파|뚫|올라서|골든)/.test(c), lose = /(이탈|깨|하향|내려가|무너)/.test(c);
      if (ns.length >= 2) {
        const [a, b] = ns;
        if (brk) return { exprs: [`상향돌파(${ma(a)}, ${ma(b)})`], desc: `${a}일선이 ${b}일선을 위로 뚫음` };
        if (lose) return { exprs: [`하향돌파(${ma(a)}, ${ma(b)})`], desc: `${a}일선이 ${b}일선 아래로 내려감` };
        const below = /(아래|밑|하회|작|낮)/.test(c);
        return { exprs: [`${ma(a)} ${below ? "<" : ">"} ${ma(b)}`], desc: `${a}일선이 ${b}일선 ${below ? "아래" : "위"}` };
      }
      const n = ns[0], p = pctOf(c);
      if (p != null && /(위|이격|높|상회|넘)/.test(c))
        return { exprs: [`종가 >= ${ma(n)} * ${fmt(1 + p / 100)}`], desc: `종가가 ${n}일선보다 ${p}% 이상 위` };
      if (p != null && /(아래|낮|하회|밑)/.test(c))
        return { exprs: [`종가 <= ${ma(n)} * ${fmt(1 - p / 100)}`], desc: `종가가 ${n}일선보다 ${p}% 이상 아래` };
      if (brk) return { exprs: [`상향돌파(종가, ${ma(n)})`], desc: `종가가 ${n}일선을 아래에서 위로 뚫음` };
      if (lose) return { exprs: [`하향돌파(종가, ${ma(n)})`], desc: `종가가 ${n}일선 아래로 내려감` };
      if (/(우상향|상승\s*중|오르는|기울기)/.test(c))
        return { exprs: [`${ma(n)} > 이전(${ma(n)}, 1)`], desc: `${n}일선이 오르는 중` };
      if (/(아래|밑|하회|이하|미만)/.test(c)) return { exprs: [`종가 < ${ma(n)}`], desc: `종가가 ${n}일선 아래` };
      return { exprs: [`종가 > ${ma(n)}`], desc: `종가가 ${n}일선 위` };
    },
    function candle(c) {
      const bigUp = /장대\s*양봉/.test(c), bigDn = /장대\s*음봉/.test(c);
      const up = /양봉/.test(c), dn = /음봉/.test(c);
      const p = pctOf(c);
      if (/시가\s*(대비|보다)/.test(c) && p != null) {
        return DOWN.test(c)
          ? { exprs: [`종가 <= 시가 * ${fmt(1 - p / 100)}`], desc: `종가가 시가보다 ${p}% 이상 낮음` }
          : { exprs: [`종가 >= 시가 * ${fmt(1 + p / 100)}`], desc: `종가가 시가보다 ${p}% 이상 높음` };
      }
      if (!up && !dn) return null;
      if ((bigUp || up) && p != null) return { exprs: [`종가 >= 시가 * ${fmt(1 + p / 100)}`], desc: `시가 대비 ${p}% 이상 양봉` };
      if ((bigDn || dn) && p != null) return { exprs: [`종가 <= 시가 * ${fmt(1 - p / 100)}`], desc: `시가 대비 ${p}% 이상 음봉` };
      if (bigUp) return { exprs: ["종가 >= 시가 * 1.1"], desc: "장대양봉 (시가 대비 10% 이상)", note: "장대양봉을 '시가보다 10% 이상 높게 마감'으로 가정했습니다." };
      if (bigDn) return { exprs: ["종가 <= 시가 * 0.9"], desc: "장대음봉 (시가 대비 10% 이상 하락)", note: "장대음봉을 '시가보다 10% 이상 낮게 마감'으로 가정했습니다." };
      return up ? { exprs: ["종가 > 시가"], desc: "양봉" } : { exprs: ["종가 < 시가"], desc: "음봉" };
    },
    function price(c) {
      if (/동전주/.test(c)) return { exprs: ["종가 < 1000"], desc: "주가 1,000원 미만" };
      const unit = "(?:원|달러|불)";
      const r = rangeOf(c, unit);
      if (r && new RegExp(unit).test(c))
        return { exprs: [`종가 >= ${r[0]}`, `종가 ${r[2]} ${r[1]}`], desc: `주가 ${r[0]} ~ ${r[1]}` };
      const m = c.match(/\$\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?\s*만?)\s*(원|달러|불)/);
      if (!m) return null;
      const v = (m[1] || m[2]).replace(/\s/g, "");
      const op = cmpOf(c);
      return { exprs: [`종가 ${op} ${v}`], desc: `주가 ${v}${m[3] && m[3] !== "원" ? "달러" : m[1] ? "달러" : "원"} ${OPWORD[op]}` };
    },
    function change(c) {
      const p = pctOf(c);
      if (p == null) {
        if (/급등/.test(c)) return { exprs: ["등락률 >= 10"], desc: "등락률 10% 이상", note: "급등을 '하루 10% 이상 상승'으로 가정했습니다." };
        if (/급락/.test(c)) return { exprs: ["등락률 <= -10"], desc: "등락률 -10% 이하", note: "급락을 '하루 10% 이상 하락'으로 가정했습니다." };
        if (/(상승|올랐|오른|플러스)/.test(c) && /(마감|한\s*날|했|$)/.test(c) && !/\d/.test(c))
          return { exprs: ["등락률 > 0"], desc: "전일보다 오름" };
        if (/(하락|내렸|내린|마이너스)/.test(c) && !/\d/.test(c)) return { exprs: ["등락률 < 0"], desc: "전일보다 내림" };
        return null;
      }
      const op = cmpOf(c);
      if (DOWN.test(c)) return { exprs: [`등락률 ${flip[op]} -${p}`], desc: `전일 대비 ${p}% ${OPWORD[op]} 하락` };
      if (UP.test(c) || /(등락률|주가|종가)/.test(c)) return { exprs: [`등락률 ${op} ${p}`], desc: `전일 대비 ${p}% ${OPWORD[op]} 상승` };
      return null;
    },
  ];

  function flow(c, field) {
    if (!c.includes(field)) return null;
    const avg = c.match(/(\d+)\s*(거래일|일|주|개월|달)\s*평균/);
    const n = avg ? daysOf(avg) : null;
    const base = avg ? `이평(${field}, ${n})` : `${field}(1)`;
    const baseWord = avg ? `최근 ${n}일 평균` : "전일";
    const down = /(감소|줄|적|작)/.test(c);
    const mul = c.match(/(\d+(?:\.\d+)?)\s*배/);
    const p = pctOf(c);
    let m = null, note = null;
    if (mul) m = +mul[1];
    else if (p != null) {
      if (p >= 100 && !down) {
        m = p / 100;
        note = `'${p}%'를 ${baseWord}의 ${fmt(m)}배로 해석했습니다. ${p}% '증가'(=${fmt(1 + p / 100)}배)를 뜻했다면 숫자를 ${fmt(1 + p / 100)}(으)로 바꾸세요.`;
      } else m = down ? 1 - p / 100 : 1 + p / 100;
    }
    const op = down && !/이상/.test(c) ? "<=" : cmpOf(c);
    if (m != null)
      return { exprs: [`${field} ${op} ${base} * ${fmt(m)}`], desc: `${field}이 ${baseWord}의 ${fmt(m)}배 ${OPWORD[op]}`, note };
    // 절대 금액·수량
    const unitRe = field === "거래대금" ? /(\d+(?:\.\d+)?)\s*(조|억|만)\s*원?/ : /(\d+(?:\.\d+)?)\s*(만|억)?\s*주/;
    const a = c.match(unitRe);
    if (a) {
      const v = a[1] + (a[2] || "");
      const lhs = avg ? base : field;
      return { exprs: [`${lhs} ${op} ${v}`], desc: `${avg ? baseWord + " " : ""}${field} ${v}${field === "거래대금" ? "원" : "주"} ${OPWORD[op]}` };
    }
    if (avg) return { exprs: [`${field} ${down ? "<" : ">"} ${base}`], desc: `${field}이 ${baseWord}보다 ${down ? "적음" : "많음"}` };
    if (/(급증|폭증|터진|터졌|폭발|크게)/.test(c))
      return { exprs: [`${field} >= ${field}(1) * 3`], desc: `${field}이 전일의 3배 이상`, note: `'급증'을 전일의 3배 이상으로 가정했습니다.` };
    if (/(증가|늘|많)/.test(c)) return { exprs: [`${field} > ${field}(1)`], desc: `${field}이 전일보다 많음` };
    if (down) return { exprs: [`${field} < ${field}(1)`], desc: `${field}이 전일보다 적음` };
    return null;
  }

  // ---------------------------------------------------------------- 문장 나누기
  const BUYWORD = /(매수|샀|사서|사면|산다면|산 뒤|산 후|진입|매입|들어가)/;

  function splitParts(t) {
    const b = t.match(BUYWORD);
    if (!b) {
      const tail = t.search(/(한|인|된|난|른|린|운|던|일|할)\s*(날|때|시점|경우|종목)/);
      return { sig: tail >= 0 ? t.slice(0, tail + 1) : t, rest: "" };
    }
    const before = t.slice(0, b.index);
    let i = before.search(/(한|인|된|난|른|린|운|던|일|할)\s*(날|때|시점|경우|종목)/);
    if (i >= 0) i += 1; // 동사 어미까지는 신호에 포함
    else {
      i = before.search(/(이후|다음\s*날|익일|당일|그날|해당일|에\s*$)/);
      if (i < 0) i = before.length;
    }
    return { sig: t.slice(0, i), rest: t.slice(i) };
  }

  function splitClauses(s) {
    const marked = s
      .replace(/\s*(또는|혹은)\s*/g, " ⟂OR⟂ ")
      .replace(/(하|이|있|되|크|많|높|낮|작|적|오르|올랐|내렸|넘)?거나(?=\s|$)/g, (m, v) => (v || "") + " ⟂OR⟂ ")
      .replace(/\s*(그리고|및|또한|와\s*동시에|과\s*동시에)\s*/g, " ⟂AND⟂ ")
      .replace(/,/g, " ⟂AND⟂ ")
      .replace(/(하|이|있|되|크|많|높|낮|작|적|오르|올랐|내렸|했|넘|었|았)(고|며|면서|으면서|이면서)(?=\s|$)/g, "$1 ⟂AND⟂ ")
      .replace(/(하|이)면(?=\s|$)/g, "$1 ⟂AND⟂ ")
      .replace(/(다가|더니|한\s*뒤|한\s*후|한\s*다음)(?=\s|$)/g, " ⟂AND⟂ ")
      // 꾸밈말로 이어진 다음 조건: '하락한 시가총액 …', '이상인 거래량 …'
      .replace(/(한|인|된|은|는|난|른|린)\s+(?=(시가총액|시총|거래량|거래대금|주가|종가|rsi|\d+\s*일\s*(선|이평|이동)))/gi, "$1 ⟂AND⟂ ");
    const parts = marked.split(/(⟂OR⟂|⟂AND⟂)/);
    const out = [];
    let op = "AND";
    for (const p of parts) {
      if (p === "⟂OR⟂") { op = "OR"; continue; }
      if (p === "⟂AND⟂") { op = "AND"; continue; }
      const c = p.trim();
      if (!c) continue;
      out.push({ op: out.length ? op : null, text: c });
      op = "AND";
    }
    // 숫자·키워드가 없는 조각(예: '주가가')은 다음 조각에 붙인다
    const merged = [];
    for (let k = 0; k < out.length; k++) {
      const cur = out[k];
      if (!/[\d%]|양봉|음봉|신고가|신저가|상한가|하한가|골든|데드|배열|과매|급등|급락|급증|갭|동전주|증가|감소|많|적|위|아래|돌파|이탈/.test(cur.text) && k + 1 < out.length) {
        out[k + 1] = { op: cur.op, text: cur.text + " " + out[k + 1].text };
        continue;
      }
      merged.push(cur);
    }
    return merged;
  }

  function interpret(text) {
    for (const r of RULES) {
      const res = r(text);
      if (res) return res;
    }
    return null;
  }

  // ---------------------------------------------------------------- 매수·청산·대상
  function parseRest(rest, full) {
    const out = { buy: null, exit: {}, universe: {}, notes: [] };
    const b = rest.match(BUYWORD);
    const buyPart = b ? rest.slice(0, b.index + b[0].length) : rest;
    const after = b ? rest.slice(b.index + b[0].length) : "";
    if (/(중간|중간값|중앙|평균\s*가|\(?\s*시가\s*\+\s*종가\s*\)?\s*\/\s*2|시가와\s*종가의?\s*(중간|평균))/.test(buyPart))
      out.buy = { type: "limit", price: "(시가 + 종가) / 2", desc: "지정가 = 신호일 (시가+종가)÷2" };
    else {
      const lm = buyPart.match(/(종가|시가|저가)\s*(?:대비|보다)\s*(\d+(?:\.\d+)?)\s*%\s*(?:낮|아래|하락|싸|싼|빠진)/);
      if (lm) out.buy = { type: "limit", price: `${lm[1]} * ${fmt(1 - +lm[2] / 100)}`, desc: `지정가 = 신호일 ${lm[1]}보다 ${lm[2]}% 낮은 가격` };
      else if (/(다음\s*날|익일|이튿날|다음\s*거래일)\s*(의\s*)?시가/.test(buyPart) || /시가(에|로)/.test(buyPart))
        out.buy = { type: "next_open", desc: "다음 날 시가에 매수" };
      else if (/종가(에|로)/.test(buyPart)) out.buy = { type: "close", desc: "신호일 종가에 매수" };
      else if (b) out.notes.push("매수 방법을 찾지 못해 기본값(지정가 (시가+종가)÷2, 5일 유효)을 씁니다.");
    }
    if (out.buy && out.buy.type === "limit") {
      const v = buyPart.match(/(\d+)\s*(?:거래)?일\s*(?:동안|이내|안에|내)\s*(?:체결|주문|유효|매수)/);
      out.buy.valid = v ? +v[1] : 5;
    }
    // 청산
    // 추적손절: "고점 대비 10% 빠지면 매도" — 손절보다 먼저 찾고 그 부분은 손절 찾기에서 뺀다
    let afterS = after;
    const trm = after.match(/(?:고점|최고가|최고점|최고치|꼭지)\s*(?:대비|에서|보다|기준)?\s*(?:으로\s*)?(?:-)?\s*(\d+(?:\.\d+)?)\s*%?\s*(?:이상)?\s*(?:하락|떨어|빠지|빠진|내리|밀리)?/) ||
      after.match(/(?:추적\s*손절|트레일링\s*(?:스탑|스톱)?)\s*(?:을|를|은|는)?\s*(\d+(?:\.\d+)?)/);
    if (trm) {
      out.exit.trail = +trm[1];
      afterS = after.replace(trm[0], " ");
    }
    // 청산 조건식: "20일선 이탈하면 매도", "10일 최저가 깨지면 매도"
    const OUT = "(?:이탈|깨|깨지|붕괴|하회|밑돌|아래로|밑으로|하향\\s*돌파)";
    const mam = after.match(new RegExp(`(\\d+)\\s*일\\s*(?:이동\\s*평균\\s*선?|이평\\s*선?|선|평균\\s*선)\\s*(?:을|를|이|가)?\\s*${OUT}`));
    const lom = after.match(new RegExp(`(\\d+)\\s*일\\s*(?:최저\\s*가?|저점|신저가)\\s*(?:을|를|이|가)?\\s*${OUT}`));
    if (mam) {
      out.exit.signal = `종가 < 이평(종가, ${mam[1]})`;
      out.exit.signalDesc = `종가가 ${mam[1]}일 이동평균 아래로 내려가면 다음 날 시가에 매도`;
      afterS = afterS.replace(mam[0], " ");
    } else if (lom) {
      out.exit.signal = `종가 < 이전(최저(저가, ${lom[1]}), 1)`;
      out.exit.signalDesc = `종가가 직전 ${lom[1]}일 최저가 아래로 내려가면 다음 날 시가에 매도`;
      afterS = afterS.replace(lom[0], " ");
    } else if (/데드\s*크로스/.test(after)) {
      const dm = after.match(/(\d+)\s*일\s*(?:선)?\s*(?:이|가|과|와)?\s*(\d+)\s*일\s*(?:선)?\s*(?:을|를|의)?\s*데드\s*크로스/);
      const [a, c] = dm ? [dm[1], dm[2]] : ["5", "20"];
      out.exit.signal = `이평(종가, ${a}) < 이평(종가, ${c}) 그리고 이전(이평(종가, ${a}), 1) >= 이전(이평(종가, ${c}), 1)`;
      out.exit.signalDesc = `${a}일선이 ${c}일선을 아래로 뚫으면(데드크로스) 다음 날 시가에 매도`;
    }
    const tm = after.match(/(\d+(?:\.\d+)?)\s*%\s*(?:이상)?\s*(?:상승|오르|올라|오른|수익|익절|이익|먹|도달)/) ||
      after.match(/(?:목표|익절)\s*(?:가|수익률)?\s*(\d+(?:\.\d+)?)\s*%?/);
    if (tm) out.exit.target = +tm[1];
    const sm = afterS.match(/(?<!추적\s*)손절\s*(?:선|가|은|는|라인)?\s*(?:-)?\s*(\d+(?:\.\d+)?)/) ||
      afterS.match(/(\d+(?:\.\d+)?)\s*%\s*(?:이상)?\s*(?:하락|떨어|빠지|손실|내리)[^,.]*?(?:손절|팔|매도|정리)/) ||
      full.match(/(?<!추적\s*)손절\s*(?:선|가|은|는|라인)?\s*(?:-)?\s*(\d+(?:\.\d+)?)/);
    if (sm) out.exit.stop = +sm[1];
    const hm = after.match(/(\d+)\s*(거래일|일|주|개월|달)\s*(?:이내|안에|동안|내|까지|보유|후)/) ||
      (/한\s*달/.test(after) ? [null, "1", "달"] : null);
    if (hm) out.exit.days = daysOf(hm);
    if (/종가\s*(기준|로\s*판단|가\s*\d)/.test(after)) out.exit.basis = "종가";
    // 대상
    const kospi = /(코스피|유가증권)/.test(full), kosdaq = /코스닥/.test(full);
    if (kospi !== kosdaq) out.universe.markets = kospi ? "코스피" : "코스닥";
    const y = full.match(/(20\d\d)\s*년\s*(?:부터|이후|~)/);
    const ry = full.match(/최근\s*(\d+)\s*년/);
    if (y) out.universe.start = `${y[1]}-01-01`;
    else if (ry) out.universe.start = `${new Date().getFullYear() - +ry[1]}-01-01`;
    if (/스팩\s*(도\s*)?포함/.test(full)) out.universe.spac = true;
    if (/우선주\s*(도\s*)?포함/.test(full)) out.universe.pref = true;
    if (/(나스닥|뉴욕|미국)/.test(full)) out.notes.push("미국 종목은 앱 맨 위 데이터 선택에서 '미국'을 고른 뒤 실행하세요.");
    return out;
  }

  // ---------------------------------------------------------------- 전체
  function convert(raw) {
    const t = norm(raw || "");
    if (!t) return null;
    const { sig, rest } = splitParts(t);
    const clauses = splitClauses(sig);
    const items = [];
    let start = null;
    for (const cl of clauses) {
      const r = interpret(cl.text);
      if (r && r.error) { items.push({ op: cl.op, src: cl.text, error: r.error }); continue; }
      if (!r) { items.push({ op: cl.op, src: cl.text, unknown: true }); continue; }
      if (r.start) start = r.start;
      items.push({ op: cl.op, src: cl.text, exprs: r.exprs, desc: r.desc, note: r.note || null });
    }
    // 그리고/또는 묶기: 또는 사이의 덩어리마다 그리고로 묶음
    const ok = items.filter((x) => x.exprs);
    const groups = [];
    for (const it of ok) {
      if (!groups.length || it.op === "OR") groups.push([]);
      groups[groups.length - 1].push(...it.exprs);
    }
    const hasOr = groups.length > 1;
    const sigLines = [];
    groups.forEach((g, gi) => {
      if (hasOr) {
        const body = g.length > 1 ? `(${g.join(" 그리고 ")})` : g[0];
        sigLines.push(gi === 0 ? body : `또는 ${body}`);
      } else g.forEach((e, ei) => sigLines.push(ei === 0 && gi === 0 ? e : `그리고 ${e}`));
    });
    const restInfo = parseRest(rest, t);
    if (start && !restInfo.universe.start) restInfo.universe.start = start;
    // 명세 텍스트
    const spec = [];
    if (sigLines.length) spec.push("[신호]", ...sigLines);
    const bu = restInfo.buy;
    if (bu) {
      spec.push("", "[매수]");
      if (bu.type === "limit") spec.push(`지정가 = ${bu.price}`, `유효기간 = ${bu.valid}`);
      else spec.push(bu.type === "next_open" ? "다음날시가" : "당일종가");
    }
    const ex = restInfo.exit;
    if (Object.keys(ex).length) {
      spec.push("", "[청산]");
      if (ex.target != null) spec.push(`목표 = ${fmt(ex.target)}`);
      else if (ex.trail != null || ex.signal) spec.push("목표 = 없음");
      if (ex.basis) spec.push(`기준 = ${ex.basis}`);
      if (ex.stop != null) spec.push(`손절 = ${fmt(ex.stop)}`);
      if (ex.days != null) spec.push(`보유기간 = ${ex.days}`);
      else if (ex.trail != null || ex.signal) spec.push("보유기간 = 120");
      if (ex.trail != null) spec.push(`추적손절 = ${fmt(ex.trail)}`);
      if (ex.signal) spec.push(`청산조건 = ${ex.signal}`);
    }
    const u = restInfo.universe;
    if (Object.keys(u).length) {
      spec.push("", "[대상]");
      if (u.markets) spec.push(`시장 = ${u.markets}`);
      if (u.start) spec.push(`기간 = ${u.start} ~ 오늘`);
      if (u.spac || u.pref) {
        const ex2 = [!u.spac && "스팩", !u.pref && "우선주"].filter(Boolean);
        spec.push(`제외 = ${ex2.length ? ex2.join(", ") : "없음"}`);
      }
    }
    return {
      normalized: t,
      items,
      signalLines: hasOr ? sigLines : groups.flat(),
      oneLine: sigLines.join(" "),
      spec: sigLines.length ? spec.join("\n") + "\n" : "",
      rest: restInfo,
      restText: rest,
    };
  }

  return { convert };
})();

export const convert = Conv.convert;
