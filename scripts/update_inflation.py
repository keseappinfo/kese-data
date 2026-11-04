#!/usr/bin/env python3
"""Kese enflasyon verisi: TRY, USD, EUR ve GBP için aylık tüketici fiyat endeksini indirir.

Yalnız Python standart kütüphanesi kullanılır (kurulum gerekmez). API anahtarı gerekmez.

Kullanım (Kese klasöründe):
    python3 Public/scripts/update_inflation.py \
        --output Public/data/inflation.json \
        --copy-to App/Resources/inflation.json

Her para için tüm kaynaklar denenir; doğrulamadan geçenler arasında EN GÜNCEL olan seçilir:
    TRY: OECD (TÜİK, eski baz) + TCMB EVDS (TÜİK, 2025=100, TP.TUKFIY2025.GENEL) birbirine bağlanır.
         EVDS için yalnız API anahtarı gerekir: EVDS_KEY ortam değişkeni (komut satırına yazmak
         anahtarı kabuk geçmişine kaydeder; tercih etme).
    USD: OECD, BLS          EUR: OECD, Eurostat (prc_hicp_minr, EA)          GBP: ONS TÜFE (D7BT)

Baz yılı değişen seriler bağlanır: çakışan ay varsa o aydan, yoksa yeni bazın yılı (2025=100)
eski serinin o yılki ortalamasına eşitlenerek. En fazla 2 aylık boşluk (ör. ABD Ekim 2025: kapanma
nedeniyle yayımlanmadı) komşu ayların geometrik ortalamasıyla doldurulur ve dosyada işaretlenir.

TRYDEP (TL mevduat): "Aynı parayı TL mevduatta tutsaydın" karşılaştırması için TCMB EVDS'teki TL mevduata
uygulanan ağırlıklı ortalama faiz (akım, varsayılan 3 aya kadar vadeli: TP.TRY.MT02; EVDS_DEPOSIT_SERIES ile
değiştirilebilir) aylık ortalama olarak indirilir ve stopaj düşülerek bir "mevduat servet endeksi"ne çevrilir:
endeks[ay+1] = endeks[ay] × (1 + ((1 + yıllık faiz)^(1/12) − 1) × (1 − stopaj)). TCMB'nin oranları yıllık
bileşiktir. Stopaj tablosu aşağıda (STOPAJ); oran değişince yeni satır eklenir (BAKIM.md).

Güvenlik: Her seri ardışık ay, pozitif değer ve makul aylık değişim kontrolünden geçer.
Kontrolü geçmeyen ya da mevcut dosyadakinden eski olan seri yazılmaz; mevcut veri korunur.
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

START_YEAR = 2005
CURRENCIES = ["TRY", "USD", "EUR", "GBP"]
MAX_MONTHLY_CHANGE = {"TRY": 0.25, "USD": 0.05, "EUR": 0.05, "GBP": 0.05}
USER_AGENT = "Kese-inflation-updater/1.0"
MONTHS_EN = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


# --------------------------------------------------------------------------- yardımcılar

def make_ssl_context():
    """Sertifika doğrulaması AÇIK kalır; yalnız güvenilir kök sertifika kaynakları eklenir.

    python.org'dan kurulan macOS Python'u sistem sertifikalarını kendiliğinden görmez
    (CERTIFICATE_VERIFY_FAILED). macOS'un kendi paketi /etc/ssl/cert.pem'de durur; varsa
    certifi ve Homebrew paketleri de eklenir.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi  # isteğe bağlı
        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    for path in ("/etc/ssl/cert.pem",
                 "/opt/homebrew/etc/openssl@3/cert.pem",
                 "/usr/local/etc/openssl@3/cert.pem"):
        if os.path.exists(path):
            try:
                ctx.load_verify_locations(path)
            except (ssl.SSLError, OSError):
                pass
    return ctx


SSL_CONTEXT = make_ssl_context()


def http(url, data=None, headers=None, timeout=90):
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
        return resp.read()


def month_key(year, month):
    return f"{int(year):04d}-{int(month):02d}"


# Resmi açıklamalarla doğrulanmış yıllık enflasyon (%) — bir kaynak tanım değiştirirse (ör. TÜFE yerine
# CPIH) ya da baz kırılması sızarsa seri reddedilir. Tolerans endeks yuvarlamasından (±0,1) gelir.
# Kaynaklar: Eurostat euro göstergeleri (17.07.2026, 17.09.2026, 02.10.2026 öncü), ONS Ağustos 2025 bülteni.
REFERENCE_YOY = {
    # TÜİK açıklamaları: Aralık 2025 %30,89 · Ocak 2026 %30,65 · Şubat 2026 %31,53 · Eylül 2026 %29,73.
    # 2026 ayları eski (2003=100) ve yeni (2025=100) serinin bağlantısından geçer; bağlama hatalıysa burada yakalanır.
    "TRY": {"2025-12": 30.89, "2026-01": 30.65, "2026-02": 31.53, "2026-09": 29.73},
    "EUR": {"2026-06": 2.8, "2026-08": 3.2, "2026-09": 3.8},
    "GBP": {"2025-08": 3.8},
}
REFERENCE_TOLERANCE = 0.15


def check_references(code, months):
    for key, expected in REFERENCE_YOY.get(code, {}).items():
        y, m = key.split("-")
        prev = f"{int(y) - 1:04d}-{m}"
        if key not in months or prev not in months:
            continue          # seri o aya ulaşmıyorsa kontrol edilemez (ör. eski kaynak)
        actual = (months[key] / months[prev] - 1) * 100
        if abs(actual - expected) > REFERENCE_TOLERANCE:
            return f"{key} yıllık %{actual:.2f}, resmi açıklama %{expected} (tanım farkı olabilir)"
    return None


def validate(code, months):
    """Hata metni döner; sorun yoksa None."""
    if len(months) < 12:
        return f"yalnız {len(months)} ay var"
    keys = sorted(months)
    limit = MAX_MONTHLY_CHANGE[code]
    for prev, cur in zip(keys, keys[1:]):
        py, pm = map(int, prev.split("-"))
        expected = month_key(py + (pm == 12), 1 if pm == 12 else pm + 1)
        if cur != expected:
            return f"ay atlaması: {prev} → {cur}"
        if months[prev] <= 0 or months[cur] <= 0:
            return f"pozitif olmayan değer: {cur}"
        change = months[cur] / months[prev] - 1
        if abs(change) > limit:
            return f"olağandışı sıçrama {prev} → {cur}: %{change * 100:.1f} (baz kırılması olabilir)"
    return check_references(code, months)


def add_month(key, n=1):
    y, m = map(int, key.split("-"))
    total = y * 12 + (m - 1) + n
    return month_key(total // 12, total % 12 + 1)


def fill_gaps(months, max_gap=2):
    """En fazla `max_gap` aylık boşlukları geometrik ara değerle doldurur. (yeni sözlük, doldurulan aylar)"""
    keys = sorted(months)
    out, filled = dict(months), []
    for prev, cur in zip(keys, keys[1:]):
        missing = []
        k = add_month(prev)
        while k < cur:
            missing.append(k)
            k = add_month(k)
        if 0 < len(missing) <= max_gap:
            a, b, n = months[prev], months[cur], len(missing) + 1
            for i, key in enumerate(missing, start=1):
                out[key] = a * (b / a) ** (i / n)
                filled.append(key)
    return out, filled


def link(old, new, base_year=None):
    """Yeni bazlı seriyi eski serinin ölçeğine getirip arkasına ekler; bağlanamıyorsa None."""
    overlap = sorted(set(old) & set(new))
    if overlap:
        anchor = overlap[-1]
        factor = old[anchor] / new[anchor]
        last = anchor
    elif base_year is not None:
        year = [old.get(month_key(base_year, m)) for m in range(1, 13)]
        if None in year:
            return None
        factor = (sum(year) / 12) / 100.0   # yeni seride baz yılının ortalaması = 100
        last = max(old)
    else:
        return None
    linked = dict(old)
    for k, v in new.items():
        if k > last:
            linked[k] = v * factor
    return linked


# --------------------------------------------------------------------------- kaynaklar

# İngiltere OECD'de alınmaz: OECD'nin GBR serisi konut maliyetini içeren CPIH'e karşılık geliyor
# (Ağustos 2025: CPIH %4,1, TÜFE %3,8). Diğer ülkelerle aynı tanım için ONS TÜFE (D7BT) kullanılır.
OECD_AREAS = {"TRY": "TUR", "USD": "USA", "EUR": "EA20"}
OECD_LABEL = {"TRY": "OECD (TÜİK)", "USD": "OECD (BLS)", "EUR": "OECD (Eurostat)"}


def fetch_oecd(codes):
    areas = "+".join(OECD_AREAS[c] for c in codes)
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/"
           f"{areas}.M.N.CPI.IX._T.N._Z?startPeriod={START_YEAR}-01"
           "&dimensionAtObservation=AllDimensions&format=csvfile")
    rows = csv.DictReader(io.StringIO(http(url).decode("utf-8-sig")))
    by_area = {}
    for row in rows:
        period, value = row.get("TIME_PERIOD", ""), row.get("OBS_VALUE", "")
        if len(period) != 7 or not value:
            continue
        by_area.setdefault(row.get("REF_AREA"), {})[period] = float(value)
    return {c: by_area[OECD_AREAS[c]] for c in codes if by_area.get(OECD_AREAS[c])}


def fetch_bls():
    """ABD CPI-U, tüm kalemler, mevsimsellikten arındırılmamış (CUUR0000SA0)."""
    months = {}
    this_year = dt.date.today().year
    for start in range(START_YEAR, this_year + 1, 10):   # anahtarsız sürüm en fazla 10 yıl ister
        body = json.dumps({"seriesid": ["CUUR0000SA0"], "startyear": str(start),
                           "endyear": str(min(start + 9, this_year))}).encode()
        data = json.loads(http("https://api.bls.gov/publicAPI/v2/timeseries/data/", data=body,
                               headers={"Content-type": "application/json"}))
        if data.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS: {data.get('message')}")
        for item in data["Results"]["series"][0]["data"]:
            period = item["period"]
            if period.startswith("M") and period != "M13" and item["value"] not in ("-", ""):
                months[month_key(item["year"], period[1:])] = float(item["value"])
    return months


EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"


def _eurostat(dataset, classification, item, unit):
    """Euro bölgesi HICP, değişen kapsam (EA): genişlemeler (2026: Bulgaristan) Eurostat'ça zincirlenir."""
    url = (f"{EUROSTAT_API}{dataset}?format=JSON&lang=EN&geo=EA&{classification}={item}"
           f"&unit={unit}&sinceTimePeriod={START_YEAR}-01")
    try:
        data = json.loads(http(url))
    except urllib.error.HTTPError:
        return {}
    times = data.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
    values = data.get("value", {})
    months = {}
    for period, pos in times.items():
        v = values.get(str(pos))
        if v is not None:
            months[period.replace("M", "-") if "M" in period else period] = float(v)
    return months


def fetch_eurostat():
    # 2026'dan itibaren: ECOICOP 2 veri seti (prc_hicp_midx Aralık 2025'te donduruldu).
    for unit in ("I25", "I15"):
        months = _eurostat("prc_hicp_minr", "coicop18", "TOTAL", unit)
        if len(months) >= 12:
            return months
    # Yedek: eski veri seti (2025 sonuna kadar)
    for unit in ("I15", "I25"):
        months = _eurostat("prc_hicp_midx", "coicop", "CP00", unit)
        if len(months) >= 12:
            return months
    raise RuntimeError("Eurostat: veri bulunamadı")


def fetch_ons():
    """İngiltere CPI endeksi (D7BT, MM23)."""
    data = json.loads(http("https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7bt/mm23/data"))
    months = {}
    for item in data.get("months", []):
        year, mon = item["date"].split()
        if int(year) >= START_YEAR and item.get("value"):
            months[month_key(year, MONTHS_EN[mon.upper()[:3]])] = float(item["value"])
    return months


EVDS_KEY = os.environ.get("EVDS_KEY")
# TÜFE (2025=100) genel endeks, veri grubu bie_tukfiy2025.
EVDS_SERIES = os.environ.get("EVDS_SERIES") or "TP.TUKFIY2025.GENEL"


def fetch_evds(series=None, aggregation=None):
    """TCMB EVDS'ten aylık seri (varsayılan TÜFE 2025=100). Anahtar gerekir.
    `aggregation`: haftalık seriyi aya çevirirken ("avg" = aylık ortalama)."""
    key, series = EVDS_KEY, series or EVDS_SERIES
    if not key or not series:
        raise RuntimeError("EVDS anahtarı verilmedi (EVDS_KEY ortam değişkeni)")
    end = dt.date.today().strftime("%d-%m-%Y")
    url = (f"https://evds3.tcmb.gov.tr/igmevdsms-dis/series={series}&startDate=01-01-{START_YEAR}"
           f"&endDate={end}&type=json&frequency=5")
    if aggregation:
        url += f"&aggregationTypes={aggregation}"
    try:
        data = json.loads(http(url, headers={"key": key}))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"EVDS HTTP {e.code} (anahtar ya da seri kodu hatalı olabilir)") from None
    except json.JSONDecodeError:
        raise RuntimeError("EVDS JSON yerine başka bir yanıt döndü (anahtar geçersiz olabilir)") from None
    field = series.replace(".", "_")
    months = {}
    for item in data.get("items", []):
        if item.get(field) in (None, ""):
            continue
        year, mon = item["Tarih"].split("-")[:2]
        months[month_key(year, mon)] = float(item[field])
    return months


# --------------------------------------------------------------------------- TL mevduat

# TL mevduata uygulanan ağırlıklı ortalama faiz (akım, %, yıllık bileşik). 3 aya kadar vadeli: tasarruf
# sahiplerinin en çok kullandığı vade aralığı (aylık yenilenen mevduat dahil).
EVDS_DEPOSIT_SERIES = os.environ.get("EVDS_DEPOSIT_SERIES") or "TP.TRY.MT02"
DEPOSIT_LABEL = "TCMB EVDS (TL mevduat, 3 aya kadar, stopaj sonrası)"

# 6 aya kadar vadeli TL mevduat stopaj oranları: (geçerli olduğu ilk ay, oran). Ay içinde değişen oran o aydan
# itibaren sayılır (yaklaşık). Kaynak: GVK geçici 67. madde ve Cumhurbaşkanı kararları.
#   2013-01 … : %15 (olağan oran)          2020-10 (30.09.2020) … : %5 (geçici indirim, defalarca uzatıldı)
#   2024-05 : %7,5    2024-11 : %10    2025-02 : %15    2025-07 (9.7.2025) : %17,5
# 2018-2019'daki kısa süreli indirim tarihleri kaynaklarda net olmadığı için olağan oran sayıldı.
# YENİ KARAR ÇIKINCA buraya satır ekle (BAKIM.md → "Stopaj değişti").
STOPAJ = [
    ("2005-01", 0.15),
    ("2020-10", 0.05),
    ("2024-05", 0.075),
    ("2024-11", 0.10),
    ("2025-02", 0.15),
    ("2025-07", 0.175),
]


def stopaj(month):
    rate = STOPAJ[0][1]
    for start, value in STOPAJ:
        if month >= start:
            rate = value
    return rate


def next_month(key):
    y, m = int(key[:4]), int(key[5:])
    return month_key(y + (m == 12), 1 if m == 12 else m + 1)


def deposit_index(rates, today=None):
    """Aylık ortalama faizden (yıllık bileşik %) stopaj sonrası birikim endeksi.
    Endeks ayın başındaki değerdir; içinde bulunulan (bitmemiş) ayın faizi işlenmez.
    Boşluk varsa yalnız en son kesintisiz bölüm kullanılır."""
    today = today or dt.date.today()
    current = month_key(today.year, today.month)
    months = sorted(m for m, r in rates.items() if r is not None and 0 < r < 200 and m < current)
    if not months:
        return {}
    # En son kesintisiz bölüm
    start = 0
    for i in range(1, len(months)):
        if next_month(months[i - 1]) != months[i]:
            start = i
    months = months[start:]
    index = {months[0]: 100.0}
    for m in months:
        gross = (1 + rates[m] / 100) ** (1 / 12) - 1
        index[next_month(m)] = index[m] * (1 + gross * (1 - stopaj(m)))
    return index


FALLBACKS = {
    "USD": [("BLS (CPI-U)", fetch_bls)],
    "EUR": [("Eurostat (HICP)", fetch_eurostat)],
    "GBP": [("ONS (CPI)", fetch_ons)],
    "TRY": [],
}


# --------------------------------------------------------------------------- ana akış

def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "generatedAt": None, "series": {}}


def main():
    global EVDS_KEY, EVDS_SERIES
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, help="yazılacak inflation.json")
    parser.add_argument("--copy-to", action="append", default=[], help="aynı dosyanın kopyalanacağı ek yol")
    parser.add_argument("--evds-key", help="TCMB EVDS API anahtarı (ya da EVDS_KEY)")
    parser.add_argument("--evds-series", help="EVDS seri kodu (varsayılan TP.TUKFIY2025.GENEL)")
    parser.add_argument("--evds-deposit-series", help="TL mevduat faizi seri kodu (varsayılan TP.TRY.MT02)")
    args = parser.parse_args()
    EVDS_KEY = args.evds_key or EVDS_KEY
    EVDS_SERIES = args.evds_series or EVDS_SERIES
    global EVDS_DEPOSIT_SERIES
    EVDS_DEPOSIT_SERIES = args.evds_deposit_series or EVDS_DEPOSIT_SERIES

    current = load(args.output)
    series = dict(current.get("series", {}))
    report = []

    raw = {c: [] for c in CURRENCIES}           # (etiket, aylar)
    try:
        for code, months in fetch_oecd(list(OECD_AREAS)).items():
            raw[code].append((OECD_LABEL[code], months))
    except Exception as e:  # noqa: BLE001 — kaynak hatası tüm betiği durdurmamalı
        report.append(f"OECD alınamadı: {e}")
    for code in CURRENCIES:
        for label, fetch in FALLBACKS[code]:
            try:
                raw[code].append((label, fetch()))
            except Exception as e:  # noqa: BLE001
                report.append(f"{code}: {label} alınamadı: {e}")

    # TRY: OECD'nin eski bazlı serisine EVDS'in 2025=100 serisini bağla
    try:
        evds = fetch_evds()
        oecd_try = next((m for label, m in raw["TRY"] if label.startswith("OECD")), None)
        linked = link(oecd_try, evds, base_year=2025) if oecd_try else None
        if linked:
            raw["TRY"].insert(0, ("OECD + TCMB EVDS (TÜİK)", linked))
        else:
            report.append("TRY: EVDS verisi OECD serisine bağlanamadı")
    except Exception as e:  # noqa: BLE001
        report.append(f"TRY: EVDS alınamadı: {e}")

    # TL mevduat endeksi (karşılaştırma için; enflasyon serisi değildir, başarısız olursa betik durmaz)
    try:
        rates = fetch_evds(EVDS_DEPOSIT_SERIES, aggregation="avg")
        index = deposit_index(rates)
        if len(index) < 13:
            raise RuntimeError(f"yeterli ay yok ({len(index)})")
        changes = [index[b] / index[a] - 1 for a, b in zip(sorted(index), sorted(index)[1:])]
        if not all(0 <= c < 0.10 for c in changes):
            raise RuntimeError("aylık getiri makul aralıkta değil")
        latest_rate = rates.get(max(m for m in rates if m < max(index)), None)
        series["TRYDEP"] = {"source": DEPOSIT_LABEL,
                            "months": {k: round(index[k], 6) for k in sorted(index)}}
        report.append(f"TRYDEP: {DEPOSIT_LABEL} ✓ {min(index)} … {max(index)} "
                      f"(son ay faizi %{latest_rate:.2f}, stopaj %{stopaj(max(index)) * 100:g})")
    except Exception as e:  # noqa: BLE001
        report.append(f"TRYDEP: mevduat faizi alınamadı, mevcut veri korundu: {e}")

    for code in CURRENCIES:
        existing = series.get(code)
        existing_latest = max(existing["months"]) if existing and existing.get("months") else None
        valid = []
        for label, months in raw[code]:
            months, filled = fill_gaps(months)
            problem = validate(code, months)
            if problem:
                report.append(f"{code}: {label} reddedildi: {problem}")
                continue
            valid.append((max(months), label, months, filled))
        if not valid:
            report.append(f"{code}: mevcut veri korundu (son ay {existing_latest})" if existing else f"{code}: VERİ YOK")
            continue
        # En güncel olan; eşitlikte listedeki ilk (OECD) kaynak
        latest, label, months, filled = max(valid, key=lambda v: (v[0], -valid.index(v)))
        if existing_latest and latest < existing_latest:
            report.append(f"{code}: yeni veri mevcut veriden eski ({latest} < {existing_latest}), korundu")
            continue
        entry = {"source": label, "months": {k: round(months[k], 6) for k in sorted(months)}}
        if filled:
            entry["interpolated"] = filled
        series[code] = entry
        note = f", ara değer: {', '.join(filled)}" if filled else ""
        report.append(f"{code}: {label} ✓ {min(months)} … {latest} ({len(months)} ay{note})")

    today = dt.date.today()
    for code, entry in series.items():
        lag = (today.year * 12 + today.month) - (int(max(entry["months"])[:4]) * 12 + int(max(entry["months"])[5:]))
        if lag > 4:
            report.append(f"UYARI: {code} verisi {lag} ay geride; uygulama 4 aydan eski veriyi göstermez.")

    print("\n".join(report))
    missing = [c for c in CURRENCIES if c not in series]

    if series == current.get("series"):
        print("Değişiklik yok; dosya yazılmadı.")
    else:
        out = {
            "version": 1,
            # Swift'in .iso8601 çözücüsü kesirli saniye kabul etmez.
            "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "series": {c: series[c] for c in [*CURRENCIES, "TRYDEP"] if c in series},
        }
        text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
        for path in [args.output, *args.copy_to]:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"Yazıldı: {path}")

    if missing:
        print(f"HATA: şu paralar için hiç veri yok: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
