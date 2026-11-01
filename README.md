# Hasad Views

يعالج هذا المشروع روابط قناة `@hasadalyoum` مباشرة عبر Playwright من خلال Workflow تشغيل يدوي مباشر.

## الإعداد والتشغيل
أضف أسرار الحسابات التسعة من Settings > Secrets and variables > Actions:
- الحساب 1: `SITE_USERNAME_1` و`SITE_PASSWORD_1`
- الحساب 2: `SITE_USERNAME_2` و`SITE_PASSWORD_2`
- الحساب 3: `SITE_USERNAME_3` و`SITE_PASSWORD_3`
- الحساب 4: `SITE_USERNAME_4` و`SITE_PASSWORD_4`
- الحساب 5: `SITE_USERNAME_5` و`SITE_PASSWORD_5`
- الحساب 6: `SITE_USERNAME_6` و`SITE_PASSWORD_6`
- الحساب 7: `SITE_USERNAME_7` و`SITE_PASSWORD_7`
- الحساب 8: `SITE_USERNAME_8` و`SITE_PASSWORD_8`
- الحساب 9: `SITE_USERNAME_9` و`SITE_PASSWORD_9`

للتشغيل المباشر اليدوي: Actions > Hasad Views Standalone > Run workflow. لإعادة محاولة رابط يدوياً: Actions > Hasad Views - Manual Link Retry > Run workflow.

## منطق المعالجة
- يكتشف كل تشغيل الروابط الجديدة ويضم إليها الدفعة المحفوظة من التشغيل السابق؛ وإذا لم تظهر روابط جديدة، يعالج الروابط المؤهلة المتبقية من الدفعة السابقة.
- يعالج التشغيل حتى تسعة طلبات كحد أقصى وبحساب واحد، مع تدوير الحسابات التسعة بالتتابع. لا يُعاد الرابط على الحساب نفسه.
- يُحفظ نجاح كل رابط حتى حد تسعة نجاحات، ويُتخطى بعد ثلاث إخفاقات. كما ينتظر السكربت عشوائياً من 40 إلى 150 ثانية بين الطلبات.
- تُحفظ الحالة في ملفات مجلد `state/`، ولا تُحفظ بيانات الدخول فيها. الـworkflow يعمل مباشرةً عبر Xvfb ولا يستدعي الطابور.

## إيقاف واستئناف النشر
- عند فشل تسجيل الدخول أو اكتشاف مشكلة بالحساب/الرصيد، يتوقف التشغيل فورًا برمز خروج `1`، ويحفظ `state/automation_paused` ويعطّل workflows النشر. تُمنع إعادة المحاولة اليدوية والتشغيلات اللاحقة أثناء الإيقاف؛ وفشل الرابط العادي لا يوقف الجدولة.
- بعد إصلاح الحساب/الرصيد، يكون الاستئناف يدويًا فقط: احذف `state/automation_paused` وأعد تفعيل `run.yml` و`run-standalone.yml` يدويًا من GitHub Actions.

## تصفير الحالة
احذف ملفات الحالة داخل `state/` (ما عدا `.gitkeep`) لبدء المعالجة من جديد.
