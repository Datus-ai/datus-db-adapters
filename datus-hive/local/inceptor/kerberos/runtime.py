"""Local HS2-only Kerberos settings, executed with the vendor's Python 3.6.

HDFS/YARN/ZooKeeper retain SIMPLE authentication. Manager configuration
regeneration requires reapplying kerberos-enable. This is a local test fixture.
"""

import json
import shutil
import socket
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def apply(mode, sid, config_root=Path("/etc"), data_root=Path("/opt/transwarp/data/datus-kerberos-test")):
    if mode not in {"enable", "restore"} or not sid.isalnum():
        raise ValueError("Invalid runtime action or service SID")
    if mode == "enable":
        (data_root / "fixedwhitelist").write_text(socket.gethostbyname(socket.gethostname()) + "\n127.0.0.1\n")
    hadoop = {
        "yarn.resourcemanager.principal": "hive/localhost@DATUS.TEST",
        "mapreduce.jobtracker.kerberos.principal": "hive/localhost@DATUS.TEST",
        "hadoop.security.authentication": "kerberos",
        "ipc.client.fallback-to-simple-auth-allowed": "true",
        "dfs.trustedchannel.resolver.class": "org.apache.hadoop.hdfs.protocol.datatransfer.WhitelistBasedTrustedChannelResolver",
        "dfs.datatransfer.client.fixedwhitelist.file": str(data_root / "fixedwhitelist"),
        "dfs.datatransfer.server.fixedwhitelist.file": str(data_root / "fixedwhitelist"),
    }
    conf = config_root / sid / "conf"
    settings = {
        conf / "hive-site.xml": dict(
            hadoop,
            **{
                "hive.server2.authentication": "KERBEROS",
                "hive.server2.authentication.kerberos.principal": "hive/localhost@DATUS.TEST",
                "hive.server2.authentication.kerberos.keytab": str(data_root / "server.keytab"),
                "hive.metastore.sasl.enabled": "false",
                "hive.server2.enable.doAs": "false",
                "hive.server2.support.dynamic.service.discovery": "false",
                "inceptor.server2.support.dynamic.service.discovery": "false",
            },
        ),
        conf / "core-site.xml": hadoop,
    }
    for path, values in settings.items():
        backup = path.with_name(path.name + ".before-datus-kerberos-test")
        if mode == "restore":
            if not backup.exists():
                raise RuntimeError("Missing original runtime file: " + str(backup))
            shutil.copy2(str(backup), str(path))
            continue
        if not backup.exists():
            shutil.copy2(str(path), str(backup))
        root = ET.fromstring(backup.read_bytes())
        for prop in list(root.findall("property")):
            if prop.findtext("name") in values:
                root.remove(prop)
        for key, value in values.items():
            prop = ET.SubElement(root, "property")
            ET.SubElement(prop, "name").text = key
            ET.SubElement(prop, "value").text = value
        path.write_bytes(ET.tostring(root, encoding="UTF-8"))
    path = conf / "ngmr-env.sh"
    backup = path.with_name("ngmr-env.sh.before-datus-kerberos-test")
    if mode == "enable":
        if not backup.exists():
            shutil.copy2(str(path), str(backup))
        path.write_bytes(
            backup.read_bytes()
            + b'\nexport EXTRA_DRIVER_OPTS="$EXTRA_DRIVER_OPTS -Dzookeeper.sasl.client=false"\nexport EXTRA_EXECUTOR_OPTS="$EXTRA_EXECUTOR_OPTS -Dzookeeper.sasl.client=false"\n'
        )
    else:
        shutil.copy2(str(backup), str(path))
    path = config_root / "krb5.conf"
    backup = path.with_name("krb5.conf.before-datus-kerberos-test")
    if mode == "enable":
        if not backup.exists():
            shutil.copy2(str(path), str(backup))
        shutil.copy2(str(data_root / "krb5.conf"), str(path))
        # The daemon runs as hive. This realm config is public; the keytab stays 0600.
        path.chmod(0o644)
    else:
        shutil.copy2(str(backup), str(path))
    print(json.dumps({"mode": mode, "runtime_files": [str(p) for p in settings] + [str(path)]}))


if __name__ == "__main__":
    apply(sys.argv[1], sys.argv[2])
