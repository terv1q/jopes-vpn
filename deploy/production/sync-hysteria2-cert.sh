#!/bin/sh
set -eu

source_dir=/var/lib/docker/volumes/jopesvpn-production-caddy-data/_data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/connect.arzhelper.tech
target_dir=/var/lib/docker/volumes/jopesvpn-production-hysteria2-certs/_data
certificate="$source_dir/connect.arzhelper.tech.crt"
private_key="$source_dir/connect.arzhelper.tech.key"

test -s "$certificate"
test -s "$private_key"
openssl x509 -in "$certificate" -noout -checkhost connect.arzhelper.tech -checkend 0

certificate_key_hash=$(openssl x509 -in "$certificate" -pubkey -noout | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)
private_key_hash=$(openssl pkey -in "$private_key" -pubout -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)
test "$certificate_key_hash" = "$private_key_hash"

mkdir -p "$target_dir"
chmod 700 "$target_dir"

if cmp -s "$certificate" "$target_dir/fullchain.pem" && cmp -s "$private_key" "$target_dir/privkey.pem"; then
    exit 0
fi

temporary_dir=$(mktemp -d "$target_dir/.sync.XXXXXX")
trap 'rm -rf "$temporary_dir"' EXIT HUP INT TERM
install -m 0644 "$certificate" "$temporary_dir/fullchain.pem"
install -m 0600 "$private_key" "$temporary_dir/privkey.pem"
mv "$temporary_dir/fullchain.pem" "$target_dir/fullchain.pem"
mv "$temporary_dir/privkey.pem" "$target_dir/privkey.pem"
docker restart jopesvpn-production-node
