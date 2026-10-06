#!/usr/bin/env bash
# Installa (o aggiorna) Backup Hub su Debian/Ubuntu. Va lanciato come root nel container di backup.
#
#   Da una copia locale del repo:   ./deploy/install.sh
#   Da GitHub:                      REPO_URL=https://github.com/zeniith14/backup-hub.git bash install.sh
set -euo pipefail

APP_DIR=/opt/backup-hub
CONF_DIR=/etc/backup-hub
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Lancialo come root." >&2; exit 1; }

echo "==> Pacchetti di sistema"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv git tzdata openssl >/dev/null

echo "==> Codice in $APP_DIR"
if [ -n "${REPO_URL:-}" ]; then
  if [ -d "$APP_DIR/.git" ]; then git -C "$APP_DIR" pull --ff-only
  else rm -rf "$APP_DIR"; git clone --depth 1 "$REPO_URL" "$APP_DIR"; fi
elif [ "$SRC_DIR" != "$APP_DIR" ]; then
  mkdir -p "$APP_DIR"
  cp -a "$SRC_DIR/app" "$SRC_DIR/requirements.txt" "$SRC_DIR/deploy" "$SRC_DIR/config.example.yml" "$APP_DIR/"
fi

echo "==> Ambiente Python"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Configurazione in $CONF_DIR"
mkdir -p "$CONF_DIR" /var/lib/backup-hub
NEWPASS=""
if [ ! -f "$CONF_DIR/config.yml" ]; then
  NEWPASS="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)"
  sed "s/\"cambiami\"/\"$NEWPASS\"/" "$APP_DIR/config.example.yml" > "$CONF_DIR/config.yml"
  chmod 600 "$CONF_DIR/config.yml"
fi

echo "==> Servizio systemd"
install -m 644 "$APP_DIR/deploy/backup-hub.service" /etc/systemd/system/backup-hub.service
cat > /usr/local/bin/backup-hub-update <<EOF
#!/usr/bin/env bash
set -e
if [ -d $APP_DIR/.git ]; then git -C $APP_DIR pull --ff-only; else echo "Nessun repo git in $APP_DIR (installazione da copia locale)"; fi
$APP_DIR/.venv/bin/pip install -q -r $APP_DIR/requirements.txt
systemctl restart backup-hub
echo "Backup Hub aggiornato."
EOF
chmod +x /usr/local/bin/backup-hub-update
systemctl daemon-reload
systemctl enable --now backup-hub >/dev/null 2>&1
systemctl restart backup-hub

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Fatto. Dashboard: http://$IP:8080"
if [ -n "$NEWPASS" ]; then
  echo "Password generata: $NEWPASS   (cambiala in $CONF_DIR/config.yml, poi: systemctl restart backup-hub)"
fi
echo "Config: $CONF_DIR/config.yml  |  Log servizio: journalctl -u backup-hub -f  |  Aggiornare: backup-hub-update"
