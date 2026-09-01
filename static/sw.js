const CACHE="asistencia-v5-v1";
self.addEventListener("install",e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(["/","/static/manifest.json"]))));
self.addEventListener("fetch",e=>e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request))));
