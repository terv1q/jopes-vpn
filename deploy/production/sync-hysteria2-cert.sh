#!/bin/sh
set -eu

env_file=${ENV_FILE:-/opt/jopesvpn/deploy/production/.env}
connect_domain=${CONNECT_DOMAIN:-$(awk -F= '$1 == "CONNECT_DOMAIN" {print $2; exit}' "$env_file" | tr -d '\r"')}
printf '%s' "$connect_domain" | grep -Eq '^([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$'
caddy_data_dir=${CADDY_DATA_DIR:-/var/lib/docker/volumes/jopesvpn-production-caddy-data/_data}
source_dir="$caddy_data_dir/caddy/certificates/acme-v02.api.letsencrypt.org-directory/$connect_domain"
target_dir=${HYSTERIA2_CERT_DIR:-/var/lib/docker/volumes/jopesvpn-production-hysteria2-certs/_data}
certificate="$source_dir/$connect_domain.crt"
private_key="$source_dir/$connect_domain.key"

test -s "$certificate"
test -s "$private_key"
openssl x509 -in "$certificate" -noout -checkhost "$connect_domain" -checkend 0

certificate_key_hash=$(openssl x509 -in "$certificate" -pubkey -noout | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)
private_key_hash=$(openssl pkey -in "$private_key" -pubout -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)
test "$certificate_key_hash" = "$private_key_hash"

mkdir -p "$target_dir"
chmod 700 "$target_dir"

if cmp -s "$certificate" "$target_dir/fullchain.pem" && cmp -s "$private_key" "$target_dir/privkey.pem"; then
    exit 0
fi

temporary_dir=$(mktemp -d "$target_dir/.sync.XXXXXX")
cleanup() {
    rm -f "$temporary_dir/fullchain.pem" "$temporary_dir/privkey.pem"
    rmdir "$temporary_dir"
}
trap cleanup EXIT HUP INT TERM
install -m 0644 "$certificate" "$temporary_dir/fullchain.pem"
install -m 0600 "$private_key" "$temporary_dir/privkey.pem"
mv "$temporary_dir/fullchain.pem" "$target_dir/fullchain.pem"
mv "$temporary_dir/privkey.pem" "$target_dir/privkey.pem"
docker restart jopesvpn-production-node
