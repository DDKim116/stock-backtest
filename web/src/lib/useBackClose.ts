import { useEffect } from "react";

// 화면 위에 띄운 창(차트 등)을 휴대폰 '뒤로 가기'로 닫을 수 있게 한다.
// open 할 때 push() 로 기록을 하나 쌓고, 닫기 버튼은 close() 로 그 기록을 되돌린다.
export function useBackClose(isOpen: boolean, onClosed: () => void) {
  useEffect(() => {
    if (!isOpen) return;
    const onPop = () => onClosed();
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [isOpen, onClosed]);
  return {
    push: () => window.history.pushState({ sbtOverlay: true }, ""),
    close: () => {
      if (window.history.state?.sbtOverlay) window.history.back();
      else onClosed();
    },
  };
}
