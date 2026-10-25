#!/bin/bash
set -euo pipefail
test "$(id -u)" -eq 0
deploy_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh -n "$deploy_root/sync-hysteria2-cert.sh"
install -m 0755 "$deploy_root/sync-hysteria2-cert.sh" /usr/local/sbin/jopesvpn-sync-hysteria2-cert
install -m 0644 "$deploy_root/jopesvpn-hysteria2-cert-sync.service" /etc/systemd/system/jopesvpn-hysteria2-cert-sync.service
install -m 0644 "$deploy_root/jopesvpn-hysteria2-cert-sync.timer" /etc/systemd/system/jopesvpn-hysteria2-cert-sync.timer
systemctl daemon-reload
systemctl enable --now jopesvpn-hysteria2-cert-sync.timer
systemctl restart jopesvpn-hysteria2-cert-sync.service
systemctl is-active --quiet jopesvpn-hysteria2-cert-sync.timer
test "$(systemctl show -p ExecMainStatus --value jopesvpn-hysteria2-cert-sync.service)" = 0
printf '%s\n' 'Certificate helper installed; timer active; certificate sync succeeded.'
