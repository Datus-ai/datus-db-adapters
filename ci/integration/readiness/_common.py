from typing import Protocol


class TestableConnector(Protocol):
    def test_connection(self) -> bool: ...

    def close(self) -> None: ...


def require_connection(adapter: str, connector: TestableConnector) -> None:
    try:
        if not connector.test_connection():
            raise RuntimeError(f"{adapter} connector readiness test returned false")
    finally:
        connector.close()
