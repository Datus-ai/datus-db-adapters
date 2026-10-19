set -eu
umask 077
export KRB5_CONFIG=/test/kdc-client.conf
export KRB5_KDC_PROFILE=/test/kdc.conf
if [ ! -f /test/principal ]; then
    kdb5_util create -s -P "$(cat /test/master-password)" -r DATUS.TEST
    kadmin.local -r DATUS.TEST -q 'addprinc -randkey hive@DATUS.TEST'
    kadmin.local -r DATUS.TEST -q 'addprinc -randkey hive/localhost@DATUS.TEST'
    kadmin.local -r DATUS.TEST -q 'ktadd -k /test/client.keytab hive@DATUS.TEST'
    kadmin.local -r DATUS.TEST -q 'ktadd -k /test/server.keytab hive/localhost@DATUS.TEST'
fi
# An interrupted bootstrap must fail rather than silently regenerate principals.
test -s /test/client.keytab && test -s /test/server.keytab
exec krb5kdc -n -r DATUS.TEST
