# راهنمای نصب و راه‌اندازی سرویس WireGuard

این راهنما از سرور خالی تا اتصال پنج Node به پنل را قدم‌به‌قدم توضیح می‌دهد. ساختار نصب شامل یک سرور مرکزی برای API، پایگاه داده، Worker و پنل مدیریتی است و یک سرور Node برای هر موقعیت که WireGuard و Agent روی آن اجرا می‌شوند.

> این نسخه هسته مدیریت Node، Interface، کاربر، طرح، سفارش، اشتراک و Peer را دارد. پنل وب فعلاً برای Node و Interface است؛ ساخت طرح و گردش خرید از API انجام می‌شود. اتصال واقعی درگاه پرداخت هنوز باید برای درگاه انتخابی پیاده‌سازی و پیش از فروش عمومی آزمایش شود. بخش «آمادگی برای فروش» را پیش از دریافت پول بخوانید.

## نمای کلی

~~~text
سامانه فروش شما
     | HTTPS و API Key محدود
     v
api.example.com: Caddy -> API + پنل (/panel) + مستند (/docs)
                         | PostgreSQL
                         | Worker صف کارها
                         +--- HTTPS ---+--- HTTPS ---+
                             Node DE       Node TR ...
                             WireGuard     WireGuard
                             Agent         Agent
                             UDP 51820     UDP 51820
~~~

هر Node یک VPS مستقل و یک دامنه Agent دارد. دامنه API مرکزی و دامنه Agentها باید به IP سرور مربوط اشاره کنند؛ برای مثال api.example.com برای سرور مرکزی و agent-de.example.com برای Node آلمان.

## پیش‌نیازها

- یک VPS مرکزی Ubuntu و برای هر موقعیت یک VPS Node؛ دسترسی root یا sudo.
- دامنه و امکان ساخت DNS A record برای سرور مرکزی و هر Node.
- TCP پورت‌های 80 و 443 در firewall ارائه‌دهنده؛ روی Nodeها UDP پورت WireGuard، پیش‌فرض 51820.
- Docker Engine و Docker Compose روی هر سرور. راهنمای رسمی: [نصب Docker روی Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
- Caddy روی سرور مرکزی برای TLS خودکار. Caddy روی Nodeها در Compose اجرا می‌شود.

Firewall ارائه‌دهنده VPS را هم تنظیم کنید؛ بازکردن پورت فقط در UFW ممکن است کافی نباشد. Docker ممکن است ترافیک پورت‌های منتشرشده را مستقل از بعضی قوانین UFW عبور دهد. قبل از تغییر قوانین firewall، تنظیمات فعلی را بررسی کنید.

## بخش اول: سرور مرکزی

در مثال‌ها api.example.com را با دامنه خودتان جایگزین کنید.

### ۱. DNS و پورت‌ها

یک رکورد DNS از نوع A برای api.example.com به IPv4 سرور مرکزی بسازید. مطمئن شوید TCP پورت‌های 80 و 443 در firewall ارائه‌دهنده باز هستند. API داخل Docker فقط روی 127.0.0.1:8000 منتشر می‌شود؛ پورت 8000 را عمومی نکنید.

### ۲. نصب Docker (اگر نصب نیست)

اگر Docker از قبل نصب است، با docker --version و docker compose version بررسی کنید و این مرحله را رد کنید. دستورهای Ubuntu زیر از مخزن رسمی Docker نصب می‌کنند:

~~~sh
apt update
apt install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version
docker compose version
~~~

### ۳. دریافت پروژه و ساخت تنظیمات محرمانه

آدرس نمونه مخزن را با URL واقعی Git جایگزین کنید:

~~~sh
apt install -y git
git clone <آدرس-مخزن-گیت> /opt/wg-panel
cd /opt/wg-panel
python3 deploy/create-central-env.py
~~~

اسکریپت .env را با secretهای تصادفی می‌سازد و دسترسی فایل را محدود می‌کند. مقادیر را چاپ یا ارسال نکنید و فایل را commit نکنید. اگر Python 3 نصب نیست، ابتدا آن را با package manager اوبونتو نصب کنید. اسکریپت فایل موجود را بازنویسی نمی‌کند.

### ۴. اجرای API، دیتابیس و Worker

~~~sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/health
~~~

باید سرویس‌های db، api و worker بالا باشند و health مقدار "status":"ok" بدهد. Migration دیتابیس هنگام شروع API اجرا می‌شود؛ PostgreSQL و داده‌های آن در volume پایدار Compose هستند.

### ۵. نصب Caddy و فعال‌کردن HTTPS

اگر Caddy نصب است به تنظیم Caddyfile بروید. در غیر این صورت مخزن رسمی Caddy را نصب کنید:

~~~sh
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install -y caddy
~~~

در /etc/caddy/Caddyfile این سایت را اضافه کنید و دامنه نمونه را عوض کنید. اگر فایل سایت‌های دیگری دارد، آن‌ها را نگه دارید:

~~~caddyfile
api.example.com {
    reverse_proxy 127.0.0.1:8000
}
~~~

~~~sh
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
curl -fsS https://api.example.com/health
~~~

پنل در https://api.example.com/panel و مستند تعاملی API در https://api.example.com/docs است.

### ۶. ساخت Tenant و کلیدهای محدود

کلید اولیه .env دسترسی کامل دارد و فقط برای راه‌اندازی است. یک Tenant و دو کلید جدا بسازید: یکی برای پنل و دیگری برای Backend فروش خودتان.

~~~sh
docker compose exec api python -m app.cli create-tenant main-service "Main Service"
docker compose exec api python -m app.cli issue-key main-service panel-admin --scopes servers:read servers:write
docker compose exec api python -m app.cli issue-key main-service storefront --scopes plans:read plans:write users:read users:write orders:create orders:read payments:confirm subscriptions:read subscriptions:renew subscriptions:write peers:read peers:write webhooks:read webhooks:write
~~~

هر کلید خام فقط یک بار نمایش داده می‌شود؛ همان موقع در password manager یا secret store امن ذخیره‌اش کنید. کلید پنل را در مرورگر وارد می‌کنید. کلید فروش را فقط در Backend امن نگه دارید، نه در JavaScript یا اپ عمومی.

بعد از ذخیره هر دو کلید، کلید اولیه را حذف و سرویس‌ها را بازسازی کنید:

~~~sh
sed -i 's/^WG_BOOTSTRAP_API_KEY=.*/WG_BOOTSTRAP_API_KEY=/' .env
docker compose up -d --force-recreate api worker
~~~

### ۷. ورود به پنل

https://api.example.com/panel را باز کنید و کلید panel-admin را وارد کنید. پنل فعلی برای مدیریت Nodeها و Interfaceهاست؛ مدیریت طرح، کاربر، سفارش و اشتراک از REST API انجام می‌شود.

## بخش دوم: نصب Node WireGuard

این بخش را روی هر VPS موقعیت جداگانه اجرا کنید. برای ۵ موقعیت به ۵ VPS و ۵ دامنه Agent نیاز دارید. چون Nodeها ماشین‌های مستقل‌اند، می‌توانند pool یکسانی مثل 10.44.0.0/24 داشته باشند.

### ۸. DNS و firewall نود

برای Node آلمان مثلاً رکورد A با نام agent-de.example.com و IP عمومی همان Node بسازید. برای ترکیه، آمریکا، روسیه و چین هم دامنه مستقل مثل agent-tr.example.com بسازید. روی هر Node TCP پورت‌های 80/443 و UDP پورت WireGuard (پیش‌فرض 51820) را در firewall ارائه‌دهنده باز کنید.

### ۹. نصب Docker و دریافت همان نسخه پروژه

اگر Docker نصب نیست، دستورهای مرحله ۲ را روی Node اجرا کنید. API و Agent باید از یک نسخه/commit باشند. سپس:

~~~sh
apt install -y git
git clone <آدرس-مخزن-گیت> /opt/wg-panel
cd /opt/wg-panel
python3 deploy/node/create-env.py
~~~

اسکریپت دامنه Agent همین Node، IP عمومی همین Node و UDP port را می‌پرسد (Enter یعنی 51820)، سپس token و کلید رمزگذاری تصادفی می‌سازد و در deploy/node/.env با permission 600 می‌نویسد. Secretها را چاپ نمی‌کند. هر Node باید فایل .env مخصوص خودش را داشته باشد؛ آن را بین Nodeها کپی نکنید.

### ۱۰. اجرای WireGuard، Agent و Caddy

~~~sh
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml config --quiet
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml up -d --build
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml ps
~~~

Compose سه سرویس هماهنگ می‌سازد: WireGuard، Agent و Caddy. WireGuard روی UDP پورت انتخابی منتشر می‌شود؛ Agent فقط روی loopback گوش می‌دهد و از اینترنت مستقیم در دسترس نیست؛ Caddy در همان network namespace درخواست HTTPS را به Agent می‌رساند. روی Node باید کل مخزن موجود باشد چون Dockerfileها از ریشه پروژه build می‌شوند.

این نصب برای کار شبکه به capabilityهای NET_ADMIN و SYS_MODULE و دسترسی خواندنی به /lib/modules نیاز دارد؛ فقط روی VPS اختصاصی و مورد اعتماد اجرا کنید. دیتای پایدار در deploy/node/data است. از این پوشه backup رمزگذاری‌شده بگیرید و .envها و کلید رمزگذاری مرکزی را نیز در secret backup امن نگه دارید.

کلید عمومی سرور WireGuard را بگیرید:

~~~sh
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml exec wireguard cat /var/lib/wg-node/server-public.key
~~~

### ۱۱. ثبت Node و Interface در پنل

در پنل مرکزی، برای هر VPS دو مورد ثبت کنید:

1. یک Node با نامی مثل Germany-1، کشور دوحرفی DE و Agent URL مثل https://agent-de.example.com بسازید.
2. فایل deploy/node/.env همان سرور را باز کنید و مقدار WG_AGENT_TOKEN را در فیلد token وارد کنید. این secret را در پیام‌رسان یا ticket نفرستید.
3. Test Connection را بزنید؛ تا وقتی موفق نشده به مرحله بعد نروید.
4. برای Node یک Interface بسازید: نام wg0، endpoint برابر IP عمومی Node و UDP port (مثلاً 203.0.113.20:51820)، کلید عمومی خروجی مرحله قبل.
5. Address pool را 10.44.0.0/24 و DNS را 1.1.1.1 بگذارید. آدرس سرور 10.45.0.1/24 است و باید از pool کلاینت جدا بماند.

جدول تکرار برای پنج کشور:

| موقعیت | کد کشور | دامنه Agent نمونه | Endpoint |
| --- | --- | --- | --- |
| آلمان | DE | agent-de.example.com | IP آلمان:51820 |
| ترکیه | TR | agent-tr.example.com | IP ترکیه:51820 |
| آمریکا | US | agent-us.example.com | IP آمریکا:51820 |
| روسیه | RU | agent-ru.example.com | IP روسیه:51820 |
| چین | CN | agent-cn.example.com | IP چین:51820 |

مراحل ۸ تا ۱۱ را برای هر VPS تکرار کنید و IP، دامنه، کلید عمومی و token همان VPS را به کار ببرید. پس از ثبت، وضعیت Node و Interface را در پنل بررسی کنید. کلاینت WireGuard نیز باید بتواند به UDP endpoint دسترسی داشته باشد.

### به‌روزرسانی Node

از ریشه مخزن و پس از بررسی تغییرات محلی:

~~~sh
git pull
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml up -d --build
~~~

## بخش سوم: طرح و سفارش از API

### ۱۲. ایجاد طرح

از https://api.example.com/docs یا Backend خودتان با کلید storefront درخواست POST /api/v1/plans بفرستید. نمونه JSON:

~~~json
{
  "name": "یک ماهه چندموقعیتی",
  "description": "یک ماه، یک دستگاه",
  "traffic_limit_bytes": 107374182400,
  "duration_days": 30,
  "peer_limit": 1,
  "price_minor": 100000,
  "currency": "IRR",
  "server_countries": ["DE", "TR", "US", "RU", "CN"]
}
~~~

traffic_limit_bytes در این نمونه ۱۰۰ گیگابایت است و باید عددی بزرگ‌تر از صفر باشد. price_minor مبلغ در کوچک‌ترین واحد پول انتخابی است. پیش از ساخت طرح، Node و Interface کشورهای موردنظر را فعال کنید. مقادیر و محدودیت‌های دقیق را در schema داخل /docs کنترل کنید.

### ۱۳. جریان خرید و تحویل کانفیگ

1. Backend شما با POST /api/v1/users و external_id پایدار کاربر را ثبت می‌کند.
2. طرح‌ها با GET /api/v1/plans خوانده می‌شوند.
3. سفارش با POST /api/v1/orders ساخته می‌شود؛ Idempotency-Key یکتا بفرستید. بدنه شامل user_id، plan_id، peer_count و کشور مثل DE است.
4. Backend شما پرداخت را مستقیماً با درگاه راستی‌آزمایی می‌کند؛ فقط پس از تأیید قطعی، POST /api/v1/orders/{id}/confirm-payment را صدا بزند.
5. Worker اشتراک و Peer را ایجاد می‌کند. وضعیت با GET /api/v1/subscriptions/{id} یا Webhook دنبال می‌شود.
6. پس از active شدن، کانفیگ از GET /api/v1/peers/{id}/config یا QR از /api/v1/peers/{id}/qr دریافت می‌شود.

پنل فعلی storefront یا صفحه پرداخت نیست؛ این تجربه را باید در وب‌سایت، اپ یا ربات فروش خودتان بسازید.

## عملیات و عیب‌یابی

روی سرور مرکزی:

~~~sh
docker compose ps
docker compose logs --tail=100 api worker
curl -fsS https://api.example.com/health
~~~

روی Node:

~~~sh
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml ps
docker compose --env-file deploy/node/.env -f deploy/node/compose.yml logs --tail=100 wireguard agent caddy
~~~

| مشکل | موارد بررسی |
| --- | --- |
| HTTPS باز نمی‌شود | DNS به IP درست، TCP 80/443، caddy validate و systemctl status caddy مرکزی؛ لاگ Caddy روی Node. |
| Health API خراب است | docker compose ps و docker compose logs --tail=100 api db؛ سپس curl http://127.0.0.1:8000/health. |
| Test Connection ناموفق است | DNS دامنه Agent به همان Node، TCP 443، سرویس‌های سالم، Agent URL و token همان Node. |
| Node وصل است ولی VPN کار نمی‌کند | UDP پورت در firewall ارائه‌دهنده، IP/پورت endpoint، کلید عمومی، pool و wg0. |
| Peer بعد restart برنگشته | حذف‌نشدن deploy/node/data و عوض‌نشدن کلیدها؛ بررسی لاگ و بازیابی backup. |
| API key گم شده | مقدار خام قابل بازیابی نیست؛ کلید تازه با scope لازم صادر و کلید قبلی را غیرفعال کنید. |

Worker را در این نسخه فقط با یک replica اجرا کنید و آن را scale نکنید. قبل از تغییر شبکه یا به‌روزرسانی، backup معتبر داشته باشید.

## امنیت و آمادگی تجاری

- Endpoint confirm-payment خودش درگاه را بررسی نمی‌کند. پیاده‌سازی درگاه باید امضا، مبلغ، ارز، شناسه سفارش و تکراری‌نبودن callback را بررسی کند و در صورت امکان استعلام server-to-server انجام دهد.
- تا پیش از ساخت این adapter، کلید دارای payments:confirm را به کاربر یا client عمومی ندهید. کلید storefront نمونه بالا برای Backend امن است، نه موبایل/مرورگر.
- UI فعلی پنل فقط Nodeها و Interfaceها را مدیریت می‌کند؛ UI کامل کاربران، اشتراک‌ها، سفارش‌ها و پرداخت‌ها موجود نیست.
- Agent را روی پورت 8787 عمومی نکنید؛ HTTPS Caddy تنها ورودی مدیریتی باشد. برای هر Node token جدا بسازید.
- فایل‌های .env، کلید Fernet، رمز PostgreSQL و داده deploy/node/data را در Git یا log قرار ندهید. backup رمزگذاری‌شده بگیرید. گم‌شدن WG_CONFIG_ENCRYPTION_KEY بازیابی secretهای رمز‌شده DB را ناممکن می‌کند.
- پیاده‌سازی فعلی IPv4 محور است و IPv6 کامل، traffic shaping و چند Worker هم‌زمان را آماده نمی‌کند. پیش از فروش عمومی، hardening، تست بازیابی، تست بار و بررسی امنیتی انجام دهید.

## Endpointهای پرکاربرد

مستند کامل و تعاملی در /docs است. همه endpointهای مدیریتی به X-API-Key با scope مناسب نیاز دارند.

| کار | Endpoint |
| --- | --- |
| طرح‌ها | GET/POST /api/v1/plans |
| کاربر | POST /api/v1/users, GET /api/v1/users/{id} |
| سفارش | POST /api/v1/orders, GET /api/v1/orders/{id} |
| تأیید پرداخت پس از راستی‌آزمایی | POST /api/v1/orders/{id}/confirm-payment |
| اشتراک و مصرف | GET /api/v1/subscriptions/{id}, GET /api/v1/subscriptions/{id}/usage |
| Peer و کانفیگ | GET /api/v1/subscriptions/{id}/peers, GET /api/v1/peers/{id}/config, GET /api/v1/peers/{id}/qr |
| Nodeها | GET/POST/PATCH /api/v1/nodes, POST /api/v1/nodes/{id}/test-connection |
| Webhook | GET/POST /api/v1/webhooks |

برای سفارش از Idempotency-Key یکتا استفاده کنید؛ جزئیات تمام Scopeها و payloadها در /docs نمایش داده می‌شود.
