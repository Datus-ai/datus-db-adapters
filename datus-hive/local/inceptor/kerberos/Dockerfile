FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends krb5-kdc krb5-admin-server krb5-user && rm -rf /var/lib/apt/lists/*
COPY init.sh /init.sh
ENTRYPOINT ["bash", "/init.sh"]
