"""Connection-local PyHive transport and query deadlines for Hive/Spark."""

from sqlalchemy import event


def configure_thrift_timeouts(engine, seconds, query_seconds):
    from thrift.transport import TSocket, TTransport

    @event.listens_for(engine, "do_connect")
    def bounded_transport(dialect, record, args, original_params):
        from pyhive.hive import get_installed_sasl

        # SQLAlchemy reuses its parameter template for subsequent connections.
        params = dict(original_params)
        host = params.pop("host", "localhost")
        port = params.pop("port", 10000)
        auth = params.pop("auth", None) or "NONE"
        password = params.pop("password", None)
        service = params.pop("kerberos_service_name", None)
        username = params.get("username")
        if auth not in ("NONE", "NOSASL", "LDAP", "CUSTOM", "KERBEROS"):
            raise ValueError(f"Unsupported Hive authentication mode: {auth}")
        if (password is not None) != (auth in ("LDAP", "CUSTOM")):
            raise ValueError("Password should be supplied only for LDAP or CUSTOM authentication")
        sock = TSocket.TSocket(host, port)
        sock.setTimeout(seconds * 1000)
        if auth == "NOSASL":
            transport = TTransport.TBufferedTransport(sock)
        else:
            import thrift_sasl

            mechanism = "GSSAPI" if auth == "KERBEROS" else "PLAIN"
            transport = thrift_sasl.TSaslClientTransport(
                lambda: get_installed_sasl(
                    host=host,
                    sasl_auth=mechanism,
                    service=service,
                    username=username,
                    password="x" if auth == "NONE" else password,
                ),
                mechanism,
                sock,
            )
        params["thrift_transport"] = transport
        try:
            return dialect.connect(*args, **params)
        except BaseException:
            transport.close()
            raise

    @event.listens_for(engine, "connect")
    def query_deadline(connection, record):
        execute = connection._client.ExecuteStatement

        def bounded_execute(request):
            request.queryTimeout = query_seconds
            return execute(request)

        connection._client.ExecuteStatement = bounded_execute

    @event.listens_for(engine, "handle_error")
    def close_failed_transport(context):
        if isinstance(context.original_exception, (TTransport.TTransportException, OSError)):
            context.is_disconnect = True
            if context.connection is not None:
                context.connection.connection.driver_connection._transport.close()
