// Service Worker بسيط لقارئ الشارت — غرضه الوحيد إنه يخلي المتصفح يعتبر الصفحة
// "تطبيق ويب" قابل للتثبيت (شرط أساسي عند كروم بالأندرويد)، مش عمل تخزين مؤقت معقد.

const CACHE_NAME = 'chart-reader-shell-v1';
const APP_SHELL = [
  '/',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // طلبات التحليل (رفع الصور) لازم تروح للسيرفر دايماً، ما بنتدخل فيها إطلاقاً
  if (event.request.method !== 'GET' || url.indexOf('/api/') !== -1) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
