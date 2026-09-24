# WireGuard Service API

سرویس چندمستاجری فروش و مدیریت اشتراک WireGuard با REST API و کنسول عملیاتی فارسی. مستند تعاملی API پس از اجرا در `/docs` و پنل در `/panel` در دسترس است.

## قابلیت‌های پیاده‌سازی‌شده

- جداسازی داده با Tenant و API Keyهای دارای Scope
- طرح، کاربر با `external_id`، سفارش و Snapshot قیمت/شرایط طرح
- تأیید پرداخت از طریق اتصال قابل‌تعویض درگاه، ساخت اشتراک و Job پایدار
- Worker جداگانه برای Provisioning، تمدید، انقضا، محاسبه مصرف و ارسال مجدد Webhook
- مدیریت چند Peer برای هر اشتراک، انتخاب خودکار سرور بر اساس کشور/سیاست طرح و تخصیص IP
- Agent برای هر سرور، ساخت/حذف Peer واقعی با `wg`, خواندن RX/TX، بازیابی Peer پس از restart
- رجیستری Node با Agent credential مستقل؛ چند Interface با pool، endpoint، کلید عمومی و DNS مستقل روی هر Node
- Agent مشترک برای Interfaceهای متعدد روی یک VPS، تست اتصال Node و نمایش وضعیت سلامت Interfaceها
- پنل فارسی برای مشاهده‌ی Nodeها و Interfaceها، ثبت Node، ساخت رکورد Interface و تست اتصال Agent
- دانلود کانفیگ، QR، ZIP و رمزگذاری کانفیگ و اسرار Agent در دیتابیس
- Quota، انقضا، تعلیق/ادامه، تمدید، API Key rotation، Audit Log و HMAC Webhook
- پاسخ و خطای API یک‌شکل، `X-Request-ID` و `Idempotency-Key` برای سفارش

## اجرای محلی API

به Python 3.11+ نیاز است.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

مقدار `WG_CONFIG_ENCRYPTION_KEY` را با یک Fernet key معتبر جایگزین کن:

```sh
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

برای `WG_BOOTSTRAP_API_KEY` از `openssl rand -hex 32` استفاده کن. این کلید دسترسی کامل مدیریتی دارد و فقط برای راه‌اندازی اولیه است. سپس:

```sh
alembic upgrade head
uvicorn app.main:app --reload
```

در ترمینال دیگری Worker را اجرا کن:

```sh
python3 -m app.worker
```

پیش‌فرض محلی SQLite است. برای استقرار از PostgreSQL استفاده کن. در این نسخه Worker را با یک replica اجرا کن؛ sweeper انقضا و تحویل webhook هنوز برای چند Worker هم‌زمان lock اختصاصی ندارند.

## Tenant و API Key

Bootstrap key در اولین درخواست یک Tenant با شناسه‌ی `WG_BOOTSTRAP_TENANT_ID` می‌سازد و تمام Scopeها را دارد. پس از راه‌اندازی، برای هر Tenant یک کلید محدود بساز و Bootstrap را از محیط Production حذف کن:

```sh
python3 -m app.cli create-tenant shop-a "Shop A"
python3 -m app.cli issue-key shop-a telegram-bot --scopes plans:read users:write orders:create subscriptions:read subscriptions:renew peers:read
python3 -m app.cli scopes
```

کلید خام فقط همان یک‌بار نمایش داده می‌شود. دیتابیس تنها SHA-256 آن را نگه می‌دارد. در درخواست‌ها از `X-API-Key` استفاده کن. Tenant از کلید استخراج می‌شود و از body قابل تغییر نیست.

## جریان خرید

1. `POST /api/v1/users` با `external_id` کاربر را می‌سازد یا همان کاربر قبلی را برمی‌گرداند.
2. `GET /api/v1/plans` طرح‌های فعال را می‌خواند.
3. `POST /api/v1/orders` سفارش را با `Idempotency-Key` می‌سازد. Body نمونه:

```json
{
  "user_id": "usr_...",
  "plan_id": "plan_...",
  "peer_count": 2,
  "country": "DE"
}
```

4. پس از تأیید واقعی پرداخت، integration درگاه `POST /api/v1/orders/{id}/confirm-payment` را با Scope `payments:confirm` فراخوانی می‌کند.
5. API سفارش را paid می‌کند و Subscription، Peerها و provisioning job را ثبت می‌کند. Worker به Agentها وصل می‌شود.
6. Client وضعیت را با `GET /api/v1/subscriptions/{id}` یا Webhook دنبال می‌کند و پس از active شدن، `GET /api/v1/peers/{id}/config` یا `/qr` را می‌گیرد.

Endpoint تأیید پرداخت خودش درگاه را verify نمی‌کند. فقط به یک payment callback/server معتبر بده دسترسی `payments:confirm`؛ هر client عمومی که این Scope را داشته باشد می‌تواند سفارش را paid اعلام کند.

## Endpointها

همه‌ی پاسخ‌های JSON با `{ "success", "data", "request_id" }` و خطاها با `{ "success": false, "error": { "code", "message" }, "request_id" } برمی‌گردند. دانلود config و QR به‌صورت فایل و با `Cache-Control: no-store` ارائه می‌شود.

| Method | Path | Scope |
| --- | --- | --- |
| GET | `/health` | عمومی |
| GET, POST | `/api/v1/plans` | `plans:read`, `plans:write` |
| PATCH, DELETE | `/api/v1/plans/{id}` | `plans:write` |
| POST | `/api/v1/users` | `users:write` |
| GET | `/api/v1/users/{id}` | `users:read` |
| PATCH | `/api/v1/users/{id}` | `users:write` |
| GET, POST, PATCH | `/api/v1/servers` | `servers:read`, `servers:write` |
| GET | `/api/v1/servers/{id}/health` | `servers:read` |
| GET, POST, PATCH | `/api/v1/nodes` | `servers:read`, `servers:write` |
| GET | `/api/v1/nodes/{id}/interfaces` | `servers:read` |
| POST | `/api/v1/nodes/{id}/test-connection` | `servers:write` |
| POST, GET | `/api/v1/orders`, `/api/v1/orders/{id}` | `orders:create`, `orders:read` |
| GET | `/api/v1/orders` | `orders:read` |
| POST | `/api/v1/orders/{id}/confirm-payment` | `payments:confirm` |
| GET | `/api/v1/subscriptions`, `/api/v1/subscriptions/{id}` | `subscriptions:read` |
| POST | `/api/v1/subscriptions/{id}/renew` | `subscriptions:renew` |
| POST | `/api/v1/subscriptions/{id}/suspend`, `/resume` | `subscriptions:write` |
| GET | `/api/v1/subscriptions/{id}/usage`, `/peers` | `subscriptions:read`, `peers:read` |
| POST | `/api/v1/subscriptions/{id}/peers` | `peers:write` |
| GET | `/api/v1/peers/{id}/config`, `/qr` | `peers:read` |
| GET | `/api/v1/peers/{id}/usage` | `peers:read` |
| POST | `/api/v1/peers/{id}/revoke`, `/recreate` | `peers:write` |
| GET | `/api/v1/subscriptions/{id}/configs.zip` | `peers:read` |
| GET, POST | `/api/v1/webhooks` | `webhooks:read`, `webhooks:write` |
| GET | `/api/v1/webhooks/{id}/deliveries` | `webhooks:read` |
| DELETE | `/api/v1/webhooks/{id}` | `webhooks:write` |
| POST, DELETE | `/api/v1/api-keys`, `/api/v1/api-keys/{id}` | `keys:write` |
| GET | `/api/v1/audit-logs`, `/api/v1/jobs/{id}` | `audit:read`, `subscriptions:read` |

## پنل مدیریت

پس از اجرای API، `/panel` را باز کن و یک API key دارای Scopeهای `servers:read` و `servers:write` وارد کن. کلید در `sessionStorage` همان tab مرورگر می‌ماند و به سرور ارسال یا در دیتابیس پنل ذخیره نمی‌شود. این کنسول فعلاً برای Nodeها و Interfaceهاست؛ امکانات مدیریتی دیگر از REST API ارائه می‌شوند.

برای ساخت Node ابتدا Agent را روی VPS نصب کن. سپس در پنل یا API یک Node با HTTPS URL و token بساز؛ token فقط در لحظه‌ی ساخت ارسال و در دیتابیس رمز می‌شود. بعد برای هر WireGuard interface روی آن VPS یک Interface تعریف کن. نمونه‌ی API:

```http
POST /api/v1/nodes
X-API-Key: <management-key>
Content-Type: application/json

{"name":"Frankfurt-1","country":"DE","agent_url":"https://node-1.example.com","agent_secret":"<32+ character random token>"}
```

```http
POST /api/v1/servers
X-API-Key: <management-key>
Content-Type: application/json

{"node_id":"node_...","interface_name":"wg0","name":"DE Premium","country":"DE","endpoint":"vpn.example.com:51820","public_key":"<wg public key>","address_pool":"10.44.0.0/24","dns":"1.1.1.1"}
```

رکورد `servers` همان Interface فروش‌پذیر است تا سازگاری API سفارش و Peer حفظ شود. Interface سیستم‌عامل و NAT/forwarding باید از قبل روی VPS ایجاد شده باشند؛ Agent به‌صورت خودکار config شبکه یا interface تازه ایجاد نمی‌کند.

غیرفعال‌کردن Node یا Interface، آن را از تخصیص‌های تازه خارج می‌کند؛ Peerهای موجود قطع نمی‌شوند و همچنان برای شمارش مصرف و اعمال سقف طرح پایش می‌شوند.

## نصب Agent روی VPS

روی هر VPS باید WireGuard، interfaceها، IP forwarding، routing/NAT و firewall متناسب با سرویس تنظیم شده باشد. Agent با یک token مشترک برای Node می‌تواند Peer را در Interfaceهای ثبت‌شده مدیریت و آمار هرکدام را جدا بخواند؛ نصب WireGuard یا ایجاد interface/NAT را انجام نمی‌دهد.

1. کد پروژه و virtualenv را در `/opt/wireguard-service` نصب کن.
2. روی همان VPS یک Agent token بلند و تصادفی و یک Fernet key جدا برای `WG_AGENT_STATE_KEY` بساز. هر دو را در `/etc/wg-agent.env` قرار بده. برای API هنگام ساخت Server، Agent token را یک‌بار وارد کن؛ API آن را با `WG_CONFIG_ENCRYPTION_KEY` رمز‌شده ذخیره می‌کند.
3. `WG_AGENT_INTERFACE=wg0` و `WG_AGENT_STATE_PATH=/var/lib/wg-agent/agent.sqlite3` را تنظیم کن.
4. `deploy/wg-agent.service` را در systemd نصب/فعال کن. Caddy یا Nginx را برای HTTPS روی دامنه Agent به `127.0.0.1:8787` reverse-proxy کن. پورت Agent را مستقیم روی اینترنت باز نکن.
5. در API ابتدا Node را با URL HTTPS و Agent token ثبت کن، سپس برای هر Interface یک `server` با `node_id`، `interface_name`، کلید عمومی (`wg show wg0 public-key`)، pool و endpoint عمومی ثبت کن.

Agent با Bearer token اختصاصی Node احراز هویت می‌کند. دیتابیس SQLite محلی Agent، کلید/کانفیگ Peer و نام Interface مربوط به آن را نگه می‌دارد تا بعد از restart شدن Agent دوباره به WireGuard اعمال شود.

## استقرار با Docker Compose

`.env` را بساز، گذرواژه PostgreSQL، bootstrap key، Fernet key و مقادیر لازم را بگذار، سپس:

```sh
docker compose up -d --build
```

API فقط روی localhost پورت `8000` منتشر می‌شود تا جلوی آن reverse proxy با TLS قرار بگیرد. PostgreSQL روی شبکه‌ی داخلی Compose است. Agentها روی VPSهای WireGuard جدا اجرا می‌شوند.

## متغیرهای محیطی

| Variable | کاربرد |
| --- | --- |
| `WG_DATABASE_URL` | SQLAlchemy URL؛ پیش‌فرض SQLite محلی |
| `WG_BOOTSTRAP_API_KEY` | کلید اولیه‌ی کامل‌دسترسی؛ در Production حذف شود |
| `WG_BOOTSTRAP_TENANT_ID` | Tenant ساخته‌شده با اولین استفاده از bootstrap key |
| `WG_CONFIG_ENCRYPTION_KEY` | Fernet key مشترک برای رمزگذاری config و Agent/Webhook secret |
| `WG_AGENT_TOKEN` | Bearer token پروسه Agent روی هر WireGuard VPS |
| `WG_AGENT_STATE_KEY` | Fernet key اختصاصی همان Agent برای رمزگذاری کانفیگ خصوصی در SQLite محلی |
| `WG_AGENT_INTERFACE` | WireGuard interface در VPS، پیش‌فرض `wg0` |
| `WG_AGENT_STATE_PATH` | مسیر دیتابیس محلی و پایدار Agent |
| `WG_WORKER_POLL_SECONDS` | فاصله‌ی polling صف |

از `WG_CONFIG_ENCRYPTION_KEY` نسخه‌ی پشتیبان امن بگیر؛ اگر گم شود کانفیگ‌های رمز‌شده قابل بازیابی نیستند. TLS برای API و ارتباط Agent اجباری نگه داشته شود.

## مرزهای نسخه‌ی فعلی

این نسخه هسته‌ی تجاری API، فروش/اشتراک، Node و Interface، Peer و پنل عملیاتی پایه را دارد؛ هنوز محصول هم‌سطح کامل WG_Panel نیست. پرداخت‌ها provider-neutral هستند و adapter درگاه (امضای callback و استعلام تراکنش) باید برای درگاه انتخابی اضافه شود. همچنین پنل کامل Peerها و Subscriptionها، پروفایل/صفحه عمومی کانفیگ، ربات تلگرام، wallet/reseller، 2FA، backup/restore، traffic control، security monitor، audit UI، OAuth، Redis و سامانه‌ی Prometheus/Grafana در این نسخه وجود ندارند. تأیید پرداخت فقط با API معتبر و Scope محدود امن است. Rate limit باید در API gateway/reverse proxy اعمال شود.
