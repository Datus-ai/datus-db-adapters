# Vendored libpq (openGauss build)

Release wheels bundle the openGauss libpq and its OpenSSL dependencies here,
under `<arch>/` (`x86_64` / `aarch64`):

- `libpq.so.5` (+ `libpq.so.5.5`)
- `libssl.so.1.1`
- `libcrypto.so.1.1`
- `MulanPSL-2.0.txt` (license of the openGauss binaries)
- `OPENSSL-LICENSE.txt` (complete OpenSSL and SSLeay license text)

The official `gaussdb` driver binds libpq via ctypes and is only compatible
with the GaussDB/openGauss build of libpq — a vanilla PostgreSQL libpq
crashes the process. `datus_gaussdb._libpq` loads the bundled copy
deterministically; set `DATUS_GAUSSDB_LIBPQ=system` (or a path) to override.

Source checkouts do not contain the binaries. Populate with:

    python scripts/fetch_vendor_libpq.py

The script extracts libpq from the pinned openGauss image and unpacks a
checksum-pinned openEuler OpenSSL update in a temporary native container.

## Build provenance

- libpq source image: `opengauss/opengauss:7.0.0-RC2.B015`
- image-list digest: `sha256:67f83a8136117cf624a78ec76736d7189db8a536b70dc3d974f168ca2af75843`
- amd64 image digest: `sha256:daccb40d63eeadc4c2a7703e8efb991f23a505c9bd3211cccfd057ba5a2fff19`
- arm64 image digest: `sha256:cc07c4e1d8ddc3b6ae5898969a2d89fd3cafe39b932b11688092d17c5e0935bc`
- OpenSSL package: `openssl-libs-1.1.1wa-17.oe2203sp4`
- maintenance source: the official openEuler 22.03 LTS SP4 update repository
- x86_64 RPM SHA256: `11033ecd81939537a4d4b84c2fb399d1fb16fdd58fa13d6a62c4e7f887b74dfd`
- aarch64 RPM SHA256: `c243aeaf739139ea78a764a3bfa166232add1f63147c2ed38b1061ed2c8eec45`

OpenSSL 1.1.1 is no longer publicly maintained upstream. The bundled ABI is
retained because this openGauss libpq requires `libssl.so.1.1`; security fixes
come from openEuler's maintained `1.1.1wa` update package. The exact RPM URLs
and checksums are pinned in `scripts/fetch_vendor_libpq.py`.
