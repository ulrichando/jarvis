// JARVIS Dispatch service worker — Web Push for phone notifications.
// Registered by components/push/push-subscribe.tsx on the /code surface.

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  const title = data.title || "Jarvis";
  const body = data.body || "";
  const url = data.url || "/code";
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/jarvis-logo.svg",
      badge: "/jarvis-logo.svg",
      data: { url },
      // Same tag → a new push replaces the old notification instead of stacking.
      tag: data.tag || undefined,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url =
    (event.notification.data && event.notification.data.url) || "/code";
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        // Reuse an open /code tab if there is one; else open a new window.
        for (const c of clients) {
          if (c.url.includes("/code") && "focus" in c) {
            if ("navigate" in c) c.navigate(url);
            return c.focus();
          }
        }
        return self.clients.openWindow(url);
      }),
  );
});
