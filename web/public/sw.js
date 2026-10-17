// 휴대폰 알림(웹 푸시) 받기. 화면 캐시는 하지 않는다.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch {
    d = { body: e.data ? e.data.text() : "" };
  }
  e.waitUntil(
    self.registration.showNotification(d.title || "주식 검증기", {
      body: d.body || "",
      icon: "/icon-192.png",
      tag: d.tag || undefined,
      renotify: !!d.tag,
      data: { tab: d.tab || "" },
    }),
  );
});

// 알림을 누르면 앱을 열고 그 탭(검증·모투 등)으로
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const tab = (e.notification.data && e.notification.data.tab) || "";
  e.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const w of wins) {
        w.postMessage({ type: "open-tab", tab });
        return w.focus();
      }
      return self.clients.openWindow("/" + (tab ? "#" + tab : ""));
    })(),
  );
});
