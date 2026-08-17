#!/usr/bin/env bash
# Generate an ephemeral CA, a server certificate signed by it, and an unrelated
# CA used by the negative verification test. The named Docker volume that holds
# these files is removed by `docker compose down -v`.
set -euo pipefail

tls_output_dir=${1:-/gaussdb-tls}
umask 077
mkdir -p "$tls_output_dir"

openssl req \
  -x509 \
  -newkey rsa:2048 \
  -nodes \
  -days 2 \
  -subj "/CN=Datus GaussDB Integration CA" \
  -keyout "$tls_output_dir/ca.key" \
  -out "$tls_output_dir/ca.crt"

openssl req \
  -newkey rsa:2048 \
  -nodes \
  -subj "/CN=localhost" \
  -keyout "$tls_output_dir/server.key" \
  -out "$tls_output_dir/server.csr"

printf '%s\n' 'subjectAltName=DNS:localhost' >"$tls_output_dir/server.ext"

openssl x509 \
  -req \
  -days 2 \
  -in "$tls_output_dir/server.csr" \
  -CA "$tls_output_dir/ca.crt" \
  -CAkey "$tls_output_dir/ca.key" \
  -CAcreateserial \
  -extfile "$tls_output_dir/server.ext" \
  -out "$tls_output_dir/server.crt"

openssl req \
  -x509 \
  -newkey rsa:2048 \
  -nodes \
  -days 2 \
  -subj "/CN=Datus GaussDB Untrusted CA" \
  -keyout "$tls_output_dir/wrong-ca.key" \
  -out "$tls_output_dir/wrong-ca.crt"

chmod 0600 "$tls_output_dir/ca.key" "$tls_output_dir/server.key" "$tls_output_dir/wrong-ca.key"
chmod 0644 "$tls_output_dir/ca.crt" "$tls_output_dir/server.crt" "$tls_output_dir/wrong-ca.crt"
# The database image runs initdb as uid/gid 70 (omm). The named volume is
# populated by this root-owned setup service, so hand the server pair to omm.
chown 70:70 "$tls_output_dir/server.key" "$tls_output_dir/server.crt"
rm -f "$tls_output_dir/server.csr" "$tls_output_dir/server.ext" "$tls_output_dir/ca.srl"
