# Hasad Views

يسحب منشورات قناة `@hasadalyoum` ويعالج الروابط مباشرة عبر Playwright، دون المرور بطابور.
جدولة Supabase تستدعي workflow `run.yml` مباشرةً بفاصل فعلي 60 دقيقة؛ يُفحص الاستحقاق كل دقيقة، وقد يتأخر الإرسال حتى دقيقة عن الموعد.

## الإعداد
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

تشغيل مباشر يدوي: Actions > Hasad Views Standalone > Run workflow.
إعادة محاولة يدوية لرابط: Actions > Hasad Views - Manual Link Retry > Run workflow.

## كيف يعمل
- يكتشف كل تشغيل الروابط الجديدة، ويضم إليها الدفعة السابقة؛ وإذا لم توجد روابط جديدة، يعيد الدفعة المحفوظة ما دام فيها روابط مؤهلة.
- يرسل كل تشغيل حتى تسعة طلبات كحد أقصى، من حساب واحد.
- يختار الحساب بالتناوب بين الحسابات التسعة `1 → 2 → … → 9 → 1`، ويحفظ المؤشر في `state/account_rotation.json`.
- لا يعيد الرابط على الحساب نفسه؛ وتُحفظ الحسابات التي حاولت الرابط في `state/link_account_history.json`.
- يسجل العداد حتى تسعة نجاحات لكل رابط في `state/success_counts.json`. بعد ثلاث إخفاقات، يُتخطى الرابط؛ وتُحفظ الإخفاقات في `state/failed_attempts.json`.
- ينتظر السكربت مدة عشوائية من 40 إلى 150 ثانية بين الطلبات.
- تُحفظ الدفعة السابقة في `state/previous_batch.json` والروابط المكتشفة في `state/processed_links.txt`.
- بيانات الدخول لا تُكتب في ملفات الحالة. طلب المشاهدات يُرسل عبر نموذج الموقع مع النقر البرمجي على زر التأكيد.

## تصفير الحالة
احذف ملفات الحالة داخل `state/` (ما عدا `.gitkeep`) لبدء المعالجة من جديد.
