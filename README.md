# راهنمای نصب و راه‌اندازی سرویس WireGuard

این راهنما سرور مرکزی و Nodeهای WireGuard را راه‌اندازی می‌کند. پنل/API با Python اجرا می‌شود و هر Node یک daemon مستقل Go دارد؛ Node ارتباط را از سمت خودش با HTTPS به پنل برقرار می‌کند.

> این نسخه هسته مدیریت Node، Interface، کاربر، طرح، سفارش، اشتراک و Peer را دارد. پنل وب فعلاً برای Node و Interface است؛ ساخت طرح و گردش خرید از API انجام می‌شود. اتصال واقعی درگاه پرداخت هنوز باید برای درگاه انتخابی پیاده‌سازی و پیش از فروش عمومی آزمایش شود. بخش «آمادگی برای فروش» را پیش از دریافت پول بخوانید.

## نمای کلی

~~~text
سامانه فروش شما
     | HTTPS و API Key محدود
     v
api.example.com: Caddy -> API + پنل (/panel) + مستند (/docs)
                         | PostgreSQL
                         | Worker صف کارها
                         ^ outbound HTTPS polling
                    Go Node DE / Node TR ...
                    WireGuard UDP 51820
~~~

هر Node یک VPS مستقل است. فقط دامنه پنل مرکزی نیاز به DNS و HTTPS دارد؛ Node دامنه Agent نمی‌خواهد و هیچ TCP مدیریتی روی آن باز نمی‌کند. از Node فقط اتصال خروجی HTTPS به پنل و ورودی UDP WireGuard لازم است. کلید Node مجزا است و در دیتابیس فقط hash آن نگه‌داری می‌شود.

## پیش‌نیازها

- یک VPS مرکزی و برای هر موقعیت یک VPS Ubuntu/Debian با دسترسی root یا sudo.
- دامنه مرکزی با HTTPS؛ برای نمونه `api.example.com`.
- TCP پورت‌های 80 و 443 روی مرکزی و UDP پورت WireGuard روی Nodeها.
- Docker Engine و Docker Compose روی سرور مرکزی. راهنمای رسمی: [نصب Docker روی Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
- Caddy روی سرور مرکزی برای TLS خودکار.

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

بعد از انتشار نسخه‌ی جدید در شاخه `main` و برای دریافت نصب‌کننده در پنل مرکزی:

~~~sh
cd /opt/wg-panel
git pull origin main
docker compose up -d --build api worker
~~~

این کار UI جدید پنل و endpoint دانلود نصاب را فعال می‌کند. آدرس پروژه در این راهنما `/opt/wg-panel` فرض شده است.

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
curl -fsS https://api.example.com/install/node.sh -o /dev/null
~~~

فرمان دوم فقط بررسی می‌کند اسکریپت نصب از API مرکزی قابل دریافت باشد.

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

این بخش را روی هر VPS موقعیت جداگانه اجرا کنید. Nodeها می‌توانند pool پیش‌فرض یکسان داشته باشند، چون هر کدام VPS و شبکه WireGuard مستقل دارند.

### ۸. DNS و firewall نود

برای Node دامنه یا TCP ورودی لازم نیست. UDP پورت WireGuard (پیش‌فرض 51820) را در firewall سیستم و ارائه‌دهنده باز کنید و اجازه اتصال خروجی TCP 443 به دامنه پنل را بدهید.

### ۹. ساخت server_api_key در پنل

در پنل وارد بخش Nodeها شوید و «نصب Node» را بزنید. نام و کد کشور را وارد کنید. پنل `NODE_ID` و `SERVER_API_KEY` اختصاصی می‌سازد؛ کلید فقط یک‌بار دیده می‌شود و برای هر Node باید کلید جدا بسازید.

### ۱۰. نصب Node با یک فرمان

روی VPS مقصد فرمانی را که پنل نمایش داده اجرا کنید. نمونه زیر را با دامنه پنل خودتان جایگزین کنید:

~~~sh
curl -fsSL https://api.example.com/install/node.sh -o /tmp/wg-node-install.sh && if [ "$(id -u)" -eq 0 ]; then bash /tmp/wg-node-install.sh; else sudo bash /tmp/wg-node-install.sh; fi
~~~

نصاب URL پنل، شناسه و کلید Node (ورودی مخفی)، IP عمومی یا DNS، پورت UDP و pool آدرس را می‌پرسد. سپس WireGuard tools و باینری Go مناسب CPU را از GitHub Release دانلود و SHA-256 را بررسی می‌کند، کلید سرور را می‌سازد، سرویس‌های `wg-quick@wg0` و `wg-node` را فعال و Interface را خودکار در پنل ثبت می‌کند. کلید در `/etc/wg-node/node.env` با permission 600 می‌ماند. مدیریت Node از طریق HTTPS خروجی انجام می‌شود.

برای نمونه‌ی پنج موقعیت، این مراحل را روی هر VPS تکرار کنید:

| موقعیت | کشور | IP/دامنه Endpoint |
| --- | --- | --- |
| آلمان | DE | IP عمومی آلمان |
| ترکیه | TR | IP عمومی ترکیه |
| آمریکا | US | IP عمومی آمریکا |
| روسیه | RU | IP عمومی روسیه |
| چین | CN | IP عمومی چین |

بعد از اتمام، وضعیت Node را در پنل بررسی و **Test Connection** را اجرا کنید. برای تونل، UDP پورت انتخابی باید در firewall ارائه‌دهنده باز باشد.

نصاب برای Ubuntu/Debian طراحی شده است. فایل محرمانه در `/etc/wg-node/node.env` و state رمزگذاری‌شده peerها در `/var/lib/wg-node/peers.json.enc` است. از این فایل‌ها backup امن بگیرید.

### به‌روزرسانی Node

برای انتشار daemon، tag مانند `wg-node-v1.0.0` بسازید و push کنید تا GitHub Actions باینری و checksum بسازد. سپس روی هر Node:

~~~sh
case "$(dpkg --print-architecture)" in
  amd64) ASSET=wg-node-linux-amd64 ;;
  arm64) ASSET=wg-node-linux-arm64 ;;
  *) echo "Unsupported architecture"; exit 1 ;;
esac
BASE=https://github.com/hosseinpv1379/wg/releases/latest/download
curl -fsSL "$BASE/$ASSET" -o /tmp/wg-node
curl -fsSL "$BASE/SHA256SUMS" -o /tmp/SHA256SUMS
(cd /tmp && grep "  $ASSET$" SHA256SUMS | sha256sum -c -)
install -m 0755 /tmp/wg-node /usr/local/bin/wg-node
systemctl restart wg-node
~~~

## بخش سوم: طرح و سفارش از API

### ۱۱. ایجاد طرح

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

### ۱۲. جریان خرید و تحویل کانفیگ

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
systemctl status wg-quick@wg0 wg-node
journalctl -u wg-node -n 100 --no-pager
wg show wg0
~~~

| مشکل | موارد بررسی |
| --- | --- |
| پنل باز نمی‌شود | DNS به IP درست، TCP 80/443، caddy validate و systemctl status caddy مرکزی. |
| Health API خراب است | docker compose ps و docker compose logs --tail=100 api db؛ سپس curl http://127.0.0.1:8000/health. |
| Test Connection ناموفق است | روی Node وضعیت wg-node، لاگ journalctl و دسترسی خروجی TCP 443 به پنل را بررسی کنید. |
| Node وصل است ولی VPN کار نمی‌کند | UDP پورت در firewall ارائه‌دهنده، IP/پورت endpoint، کلید عمومی، pool و wg0. |
| Peer بعد restart برنگشته | سرویس wg-quick@wg0 و wg-node، فایل state در /var/lib/wg-node و لاگ daemon را بررسی کنید. |
| API key گم شده | مقدار خام قابل بازیابی نیست؛ کلید تازه با scope لازم صادر و کلید قبلی را غیرفعال کنید. |

Worker را در این نسخه فقط با یک replica اجرا کنید و آن را scale نکنید. قبل از تغییر شبکه یا به‌روزرسانی، backup معتبر داشته باشید.

## امنیت و آمادگی تجاری

- Endpoint confirm-payment خودش درگاه را بررسی نمی‌کند. پیاده‌سازی درگاه باید امضا، مبلغ، ارز، شناسه سفارش و تکراری‌نبودن callback را بررسی کند و در صورت امکان استعلام server-to-server انجام دهد.
- تا پیش از ساخت این adapter، کلید دارای payments:confirm را به کاربر یا client عمومی ندهید. کلید storefront نمونه بالا برای Backend امن است، نه موبایل/مرورگر.
- UI فعلی پنل فقط Nodeها و Interfaceها را مدیریت می‌کند؛ UI کامل کاربران، اشتراک‌ها، سفارش‌ها و پرداخت‌ها موجود نیست.
- برای هر Node کلید اختصاصی بسازید و VPS را فقط از firewallهای مورد اعتماد مدیریت کنید؛ پنل روی Node هیچ پورت مدیریتی inbound ندارد.
- فایل‌های `.env`، کلید Fernet، رمز PostgreSQL، `/etc/wg-node/node.env` و state زیر `/var/lib/wg-node` را در Git یا log قرار ندهید. گم‌شدن `WG_CONFIG_ENCRYPTION_KEY` بازیابی secretهای DB را ناممکن می‌کند.
- کلید نصب خام فقط یک بار نمایش داده می‌شود؛ hash آن در DB می‌ماند. غیرفعال‌کردن Node دسترسی همان کلید را فوراً قطع می‌کند.
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
| ثبت Node و ارتباط daemon | POST /api/v1/nodes/setup؛ Node از /api/v1/node-agent/{id}/register و /commands استفاده می‌کند |
| Webhook | GET/POST /api/v1/webhooks |

برای سفارش از Idempotency-Key یکتا استفاده کنید؛ جزئیات تمام Scopeها و payloadها در /docs نمایش داده می‌شود.
