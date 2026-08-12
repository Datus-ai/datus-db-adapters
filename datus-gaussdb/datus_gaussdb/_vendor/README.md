# Vendored libpq (openGauss build)

Release wheels bundle the openGauss libpq and its OpenSSL dependencies here,
under `<arch>/` (x86_64 / aarch64):

- `libpq.so.5` (+ `libpq.so.5.5`)
- `libssl.so.1.1`
- `libcrypto.so.1.1`
- `MulanPSL-2.0.txt` (license of the openGauss binaries)

The official `gaussdb` driver binds libpq via ctypes and is only compatible
with the GaussDB/openGauss build of libpq — a vanilla PostgreSQL libpq
crashes the process. `datus_gaussdb._libpq` loads the bundled copy
deterministically; set `DATUS_GAUSSDB_LIBPQ=system` (or a path) to override.

Source checkouts do not contain the binaries. Populate with:

    python scripts/fetch_vendor_libpq.py

which extracts them from the pinned openGauss container image.
