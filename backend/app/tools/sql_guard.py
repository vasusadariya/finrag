"""Client for the Go SQL guard service. Fails closed: if the guard cannot be reached or answers
unexpectedly, nothing may be executed."""

from dataclasses import dataclass

import httpx


class SqlGuardUnavailableError(RuntimeError):
    """The guard did not give a trustworthy answer; callers must refuse to run the query."""


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str | None  # the validated, LIMIT-enforced SQL to execute (only when ok)
    reasons: tuple[str, ...]


class SqlGuardClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8081", timeout_s: float = 5.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s)

    def close(self) -> None:
        self._http.close()

    def healthy(self) -> bool:
        try:
            return self._http.get("/healthz").status_code == 200
        except httpx.HTTPError:
            return False

    def validate(self, sql: str) -> GuardResult:
        try:
            resp = self._http.post("/validate", json={"sql": sql})
        except httpx.HTTPError as e:
            raise SqlGuardUnavailableError(f"SQL guard unreachable: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise SqlGuardUnavailableError(f"SQL guard returned HTTP {resp.status_code}")
        try:
            body = resp.json()
            ok = body["ok"]
            if not isinstance(ok, bool):
                raise TypeError("ok is not a boolean")
            sql_out = body.get("sql")
            reasons = tuple(str(r) for r in body.get("reasons") or ())
        except (ValueError, KeyError, TypeError) as e:
            raise SqlGuardUnavailableError(f"SQL guard sent a malformed response: {e}") from e
        if ok and not (isinstance(sql_out, str) and sql_out.strip()):
            raise SqlGuardUnavailableError("SQL guard approved a query but returned no SQL")
        if not ok and not reasons:
            reasons = ("rejected without a reason",)
        return GuardResult(ok=ok, sql=sql_out if ok else None, reasons=reasons)
