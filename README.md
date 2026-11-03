# kese-data

Kese uygulamasının herkese açık dosyaları. Uygulamanın kaynak kodu burada **değildir**.

| Dosya | Ne işe yarar |
|---|---|
| `index.html`, `gizlilik.html` | Destek ve gizlilik sayfaları (GitHub Pages) |
| `data/inflation.json` | TRY, USD, EUR, GBP aylık tüketici fiyat endeksleri |
| `scripts/update_inflation.py` | Veriyi resmi kaynaklardan indiren ve doğrulayan betik |
| `.github/workflows/update-inflation.yml` | Betiği her gün çalıştırır; veri değişirse kaydeder |

Uygulama veriyi `https://cdn.jsdelivr.net/gh/keseappinfo/kese-data@main/data/inflation.json`
adresinden (yedek: GitHub Pages) okur. İstekler kullanıcıya ait hiçbir bilgi içermez.

## Kaynaklar
- Türkiye: OECD (TÜİK, 2025 sonuna kadar) + TCMB EVDS (TÜİK 2025=100, 2026'dan itibaren), birbirine bağlanır
- ABD: OECD / BLS CPI-U (Ekim 2025 kapanma nedeniyle yayımlanmadı; komşu aylardan ara değer)
- Euro bölgesi: Eurostat HICP `prc_hicp_minr` (EA, değişen kapsam) / OECD
- İngiltere: ONS TÜFE (D7BT). OECD'nin İngiltere serisi CPIH'e karşılık geldiği için kullanılmaz.
Her para için doğrulamadan geçen en güncel kaynak seçilir.

Betik her seriyi ardışık ay, pozitif değer ve makul aylık değişim (TRY %25, diğerleri %5) için kontrol eder.
Baz yılı değişikliği gibi bir kırılma görürse o seriyi yazmaz, eski veriyi korur ve Actions günlüğünde nedenini yazar.

## Kurulum
1. Bu klasörü `kese-data` adlı **herkese açık** bir GitHub deposu olarak yükle.
2. Settings → Pages → Branch: `main`, klasör: `/ (root)`.
3. Actions sekmesinde "Enflasyon verisini güncelle" iş akışını bir kez elle çalıştır (Run workflow).
4. Uygulamada `PublicSite.owner` değerini GitHub kullanıcı adınla değiştir (`Packages/BirikimCore/Sources/BirikimCore/PublicSite.swift`).

Kısa yol: `bash scripts/setup_repo.sh` 1–3. adımları ve EVDS anahtarını tek seferde yapar.

Not: GitHub, 60 gün hiç etkinlik olmayan herkese açık depolarda zamanlanmış iş akışlarını durdurur.
Veri her ay değiştiği için iş akışının kendi commit'leri bunu önler; yine de ara sıra Actions sekmesine göz at.
