# WG Node

برنامه‌ی مستقل Go برای مدیریت یک Interface وایرگارد در هر سرور Node است. برنامه فقط اتصال HTTPS خروجی به پنل مرکزی برقرار می‌کند، Node را ثبت می‌کند، فرمان‌ها را از صف می‌گیرد و تغییر peerها را با ابزار `wg` اعمال می‌کند. هیچ پورت TCP مدیریتی روی Node باز نمی‌شود.

## سازگاری

- باینری‌های آماده برای Linux/amd64 و Linux/arm64 در GitHub Release ساخته می‌شوند.
- نصب‌گر خودکار برای Ubuntu و Debian است؛ روی Node نهایی نیازی به Go، Python یا Docker نیست.
- برای build از سورس، Go 1.27 یا جدیدتر لازم است.
- WireGuard باید نصب باشد و Interface (معمولاً `wg0`) فعال باشد. نصب‌گر اصلی WireGuard، تنظیم شبکه و unitهای systemd را ایجاد می‌کند.

## تنظیمات

| متغیر | لازم | مقدار پیش‌فرض | کاربرد |
| --- | --- | --- | --- |
| `WG_PANEL_URL` | بله | - | آدرس HTTPS پنل مرکزی |
| `WG_NODE_ID` | بله | - | شناسه ساخته‌شده در پنل |
| `WG_SERVER_API_KEY` | بله | - | کلید اختصاصی یک‌بارنمایش Node |
| `WG_PUBLIC_HOST` | بله | - | IP یا DNS عمومی که کلاینت‌ها به آن وصل می‌شوند |
| `WG_SERVER_PORT` | خیر | `51820` | پورت UDP وایرگارد |
| `WG_INTERFACE` | خیر | `wg0` | نام Interface فعال |
| `WG_CLIENT_POOL` | خیر | `10.44.0.0/24` | شبکه IPv4 کلاینت‌ها |
| `WG_DNS` | خیر | `1.1.1.1` | DNS داخل کانفیگ کلاینت |
| `WG_STATE_PATH` | خیر | `/var/lib/wg-node/peers.json.enc` | محل state رمزگذاری‌شده‌ی peerها |

کلیدهای خصوصی peerها در state محلی با AES-GCM و کلیدی مشتق‌شده از کلید Node رمز می‌شوند. فایل state و کلید Node را جداگانه و امن backup بگیرید؛ گم‌شدن هرکدام بازیابی کانفیگ peerهای قبلی را ناممکن می‌کند.

## ساخت باینری

از ریشه مخزن:

~~~sh
cd wg-node
go test ./...
mkdir -p ../dist
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o ../dist/wg-node-linux-amd64 .
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags='-s -w' -o ../dist/wg-node-linux-arm64 .
(cd ../dist && sha256sum wg-node-linux-amd64 wg-node-linux-arm64 > SHA256SUMS)
~~~

با push تگی مانند `wg-node-v0.1.0`، GitHub Actions تست‌ها را اجرا می‌کند و این دو باینری و فایل checksum را در Release قرار می‌دهد. برای نصب خودکار Node، Release باید از قبل منتشر شده باشد.

## سرویس systemd

نصاب، WireGuard را با `wg-quick@wg0` و daemon را با `wg-node` فعال می‌کند. فایل تنظیمات در `/etc/wg-node/node.env` با دسترسی root-only قرار می‌گیرد و systemd آن را به برنامه می‌دهد. برای مشاهده یا مدیریت:

~~~sh
systemctl status wg-quick@wg0 wg-node
journalctl -u wg-node -f
wg show wg0
systemctl restart wg-node
systemctl restart wg-quick@wg0
~~~

در firewall فقط UDP پورت WireGuard را برای اتصال کلاینت‌ها باز کنید و اتصال خروجی HTTPS به دامنه‌ی پنل را مجاز بگذارید. وضعیت firewall ارائه‌دهنده را هم بررسی کنید.
