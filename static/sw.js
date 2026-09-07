// Service Worker بسيط لقارئ الشارت — غرضه الوحيد إنه يخلي المتصفح يعتبر الصفحة
// "تطبيق ويب" قابل للتثبيت (شرط أساسي عند كروم بالأندرويد). ما بيخزن أي شي مؤقتاً
// (لا الصفحة ولا أي ملف) مشان التحديثات الجديدة تظهر دايماً فوراً من غير ما تعلق
// نسخة قديمة بذاكرة المتصفح.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // تنظيف أي ذاكرة تخزين مؤقت قديمة كانت موجودة من نسخة سابقة من هاد الملف
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  // كل طلب بيروح للشبكة مباشرة دايماً — بدون تخزين مؤقت إطلاقاً
  event.respondWith(fetch(event.request));
});
