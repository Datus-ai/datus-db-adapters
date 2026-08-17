from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "run-integration-tests.sh"


def test_gaussdb_client_ca_files_are_private_before_export() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    trusted_copy = source.index(
        'docker_compose -f "$compose_file" cp gaussdb:/gaussdb-tls/ca.crt "$GAUSSDB_TLS_HOST_DIR/ca.crt"'
    )
    untrusted_copy = source.index(
        'docker_compose -f "$compose_file" cp gaussdb:/gaussdb-tls/wrong-ca.crt "$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"'
    )
    private_permissions = source.index('chmod 0600 "$GAUSSDB_TLS_HOST_DIR/ca.crt" "$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"')
    trusted_export = source.index('export GAUSSDB_SSLROOTCERT="$GAUSSDB_TLS_HOST_DIR/ca.crt"')

    assert max(trusted_copy, untrusted_copy) < private_permissions < trusted_export
