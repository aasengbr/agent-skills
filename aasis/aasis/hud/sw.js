// Service worker mínimo (exigido para instalar como app). Não faz cache: a página e a API são sempre ao vivo.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
