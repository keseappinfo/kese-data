#!/bin/bash
# Kese — herkese açık "kese-data" deposunu kurar (bir kez çalıştırılır; tekrar çalıştırmak zararsızdır).
#
#   cd ~/Desktop/Kese && bash Public/scripts/setup_repo.sh
#
# Yaptıkları: GitHub girişi (tarayıcıdan) → depoyu oluşturma ve yükleme → GitHub Pages'i açma →
# EVDS anahtarını gizli değişken olarak kaydetme (ekranda görünmeden) → veri güncellemesini bir kez başlatma.
set -euo pipefail

REPO="kese-data"
cd "$(dirname "$0")/.."          # Public/

say() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

# 1) Araçlar
if ! command -v gh >/dev/null 2>&1; then
  say "GitHub komut satırı aracı (gh) kuruluyor…"
  brew install gh
fi

# 2) Giriş
if ! gh auth status >/dev/null 2>&1; then
  say "GitHub'a giriş: tarayıcı açılacak, ekrandaki kodu gir ve onayla."
  gh auth login --hostname github.com --git-protocol https --web
fi
OWNER="$(gh api user --jq .login)"
say "GitHub kullanıcısı: $OWNER"

# 3) Kullanıcı adını uygulamaya ve belgelere yaz (KULLANICI yer tutucusu)
for f in README.md ../Packages/BirikimCore/Sources/BirikimCore/PublicSite.swift ../AppStore/metadata.md; do
  if [ -f "$f" ] && grep -q "KULLANICI" "$f"; then
    sed -i '' "s/KULLANICI\.github\.io/$(echo "$OWNER" | tr '[:upper:]' '[:lower:]').github.io/g; s/KULLANICI/$OWNER/g" "$f"
    echo "Güncellendi: $f"
  fi
done
if grep -rIl --exclude-dir=.git --exclude-dir=scripts "KULLANICI\|destek@example.com" . >/dev/null 2>&1; then
  echo "HATA: Hâlâ yer tutucu var:"
  grep -rIln --exclude-dir=.git --exclude-dir=scripts "KULLANICI\|destek@example.com" .
  exit 1
fi

# 4) Git deposu ve ilk kayıt (kişisel e-posta yerine GitHub'ın gizli "noreply" adresi kullanılır)
if [ ! -d .git ]; then
  git init -q -b main
fi
git config user.name "$OWNER"
git config user.email "$(gh api user --jq .id)+$OWNER@users.noreply.github.com"
printf '.DS_Store\n' > .gitignore
git add -A
git diff --cached --quiet || git commit -q -m "Kese herkese açık dosyaları"

# 5) GitHub'da oluştur / güncelle
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  say "Depo zaten var, güncelleniyor…"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
  git pull -q --rebase origin main 2>/dev/null || true
  git push -q -u origin main
else
  say "Herkese açık $REPO deposu oluşturuluyor…"
  gh repo create "$REPO" --public --source=. --remote=origin --push \
    --description "Kese uygulamasının herkese açık dosyaları: gizlilik/destek sayfaları ve enflasyon verisi"
fi

# 6) GitHub Pages
say "GitHub Pages açılıyor…"
gh api -X POST "repos/$OWNER/$REPO/pages" -f "source[branch]=main" -f "source[path]=/" >/dev/null 2>&1 \
  || echo "(Pages zaten açık)"

# 7) EVDS anahtarı (gizli değişken; kimse göremez)
if gh secret list --repo "$OWNER/$REPO" 2>/dev/null | grep -q '^EVDS_KEY'; then
  echo "EVDS_KEY zaten kayıtlı."
elif [ -n "${EVDS_KEY:-}" ]; then
  printf '%s' "$EVDS_KEY" | gh secret set EVDS_KEY --repo "$OWNER/$REPO"
  echo "EVDS_KEY bu Terminal penceresindeki değerden kaydedildi."
else
  say "EVDS anahtarını yapıştır ve Enter'a bas (ekranda görünmez):"
  gh secret set EVDS_KEY --repo "$OWNER/$REPO"
fi

# 8) Veri güncellemesini bir kez çalıştır
say "Enflasyon güncellemesi başlatılıyor…"
sleep 5
gh workflow run update-inflation.yml --repo "$OWNER/$REPO" \
  || echo "Başlatılamadı; birkaç dakika sonra GitHub'da Actions sekmesinden 'Run workflow' ile başlat."

say "Tamam."
echo "  Destek sayfası:  https://$(echo "$OWNER" | tr '[:upper:]' '[:lower:]').github.io/$REPO/  (yayına girmesi 1-2 dk sürebilir)"
echo "  Gizlilik:        https://$(echo "$OWNER" | tr '[:upper:]' '[:lower:]').github.io/$REPO/gizlilik.html"
echo "  İş akışı:        https://github.com/$OWNER/$REPO/actions"
