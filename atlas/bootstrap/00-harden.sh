#!/usr/bin/env bash
# Harden a fresh Debian 13 / Ubuntu 24.04 box before ATLAS is installed.
# Verified against Debian 13 (trixie), which is what atlas.example.com runs.
# Run once, as root, on atlas.example.com. Idempotent.
set -euo pipefail

SSH_PORT="${SSH_PORT:-2202}"
ADMIN_USER="${ADMIN_USER:-miro}"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }

log "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ufw fail2ban unattended-upgrades curl ca-certificates \
                       gnupg jq git chrony

log "Admin user"
if ! id -u "$ADMIN_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$ADMIN_USER"
  usermod -aG sudo "$ADMIN_USER"
fi
install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
if [[ ! -s "/home/$ADMIN_USER/.ssh/authorized_keys" ]]; then
  echo "!! /home/$ADMIN_USER/.ssh/authorized_keys is empty."
  echo "!! Add your public key NOW in another terminal, then re-run."
  exit 1
fi
chmod 600 "/home/$ADMIN_USER/.ssh/authorized_keys"
chown "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.ssh/authorized_keys"

log "SSH: key-only, no root, port ${SSH_PORT}"
cat > /etc/ssh/sshd_config.d/99-atlas.conf <<SSHD
Port ${SSH_PORT}
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
AllowUsers ${ADMIN_USER}
X11Forwarding no
MaxAuthTries 3
ClientAliveInterval 300
SSHD
sshd -t
systemctl reload ssh || systemctl reload sshd

log "Firewall"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}"/tcp comment 'ssh (closed after netbird verified)'
ufw allow 443/tcp comment 'webhooks only'
ufw allow 80/tcp  comment 'acme http-01'
ufw --force enable

log "fail2ban"
cat > /etc/fail2ban/jail.d/atlas.conf <<F2B
[sshd]
enabled  = true
port     = ${SSH_PORT}
maxretry = 3
bantime  = 1h
findtime = 10m
F2B
systemctl enable --now fail2ban
systemctl restart fail2ban

log "Unattended security upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<AUTO
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
AUTO

log "Kernel hardening"
cat > /etc/sysctl.d/99-atlas.conf <<SYS
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.tcp_syncookies=1
kernel.dmesg_restrict=1
SYS
sysctl -q --system

log "Done. SSH now on port ${SSH_PORT} as ${ADMIN_USER}."
echo "Open a NEW session and confirm it works BEFORE closing this one."
