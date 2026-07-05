"""Async client for the seestar_alp ASCOM Alpaca HTTP API.

This module speaks the ASCOM Alpaca wire protocol exposed by ``seestar_alp``
for the ZWO Seestar S50, plus the seestar_alp *action tunnel* that reaches the
device's native JSON-RPC methods.

Wire protocol summary
----------------------
Endpoints are lowercase: ``{base_url}/api/v1/telescope/{device_num}/{verb}``.

- **GET** (read properties): query params ``ClientID`` and
  ``ClientTransactionID``.
- **PUT** (set properties / invoke actions): ``x-www-form-urlencoded`` body.
- Every response is the Alpaca envelope::

    {"Value": <any>, "ClientTransactionID": <int>, "ServerTransactionID": <int>,
     "ErrorNumber": <int>, "ErrorMessage": <str>}

  ``ErrorNumber == 0`` is success. Notable non-zero codes:
    * ``1024`` NotImplemented -- ~4 of 52 GETs the Seestar lacks.
    * ``1036`` ActionNotImplemented -- bare-device rejection of ASCOM
      action-extensions (but seestar_alp's *own* ``action`` endpoint works).

The action tunnel: ``PUT .../action`` with a form body where ``Parameters`` is
a **JSON string** (not nested form fields):

    Action=method_sync&Parameters={"method":"<native>","params":[...]}&ClientID=1&...

Security / auditability
-----------------------
Every network call is routed through the injected :class:`ProvenanceLog` (when
provided). The provenance layer redacts secret-looking material, so raw request
bodies are safe to pass to it verbatim. This module holds no secrets.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .config import Settings
    from .provenance import ProvenanceLog

# ASCOM Alpaca reserved error numbers.
ERR_NOT_IMPLEMENTED = 1024  # 0x400
ERR_ACTION_NOT_IMPLEMENTED = 1036  # 0x40C


class AlpacaError(Exception):
    """An Alpaca call returned a non-zero ``ErrorNumber``."""

    def __init__(
        self,
        error_number: int,
        error_message: str,
        verb: str | None = None,
    ) -> None:
        self.error_number = error_number
        self.error_message = error_message
        self.verb = verb
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"AlpacaError(verb={self.verb!r}, "
            f"error_number={self.error_number}, "
            f"error_message={self.error_message!r})"
        )


class AlpacaNotImplemented(AlpacaError):
    """Raised when ``ErrorNumber == 1024`` (NotImplemented).

    Expected for the handful of standard ASCOM properties the Seestar does not
    support (e.g. SiteElevation). Callers should handle this gracefully.
    """


class AlpacaActionNotImplemented(AlpacaError):
    """Raised when ``ErrorNumber == 1036`` (ActionNotImplemented)."""


class AlpacaTransportError(AlpacaError):
    """Raised when the seestar_alp bridge is unreachable.

    Wraps an ``httpx.RequestError`` (connection refused, DNS failure, read
    timeout, etc.) so callers get a clean :class:`AlpacaError` — which the
    controller already maps to ``{"ok": False}`` — instead of an unhandled
    transport exception. Carries a sentinel ``error_number`` of ``-1``.
    """

    def __init__(
        self,
        error_number: int = -1,
        error_message: str = "",
        verb: str | None = None,
    ) -> None:
        super().__init__(error_number, error_message, verb)


def _alpaca_bool(value: bool) -> str:
    """Serialize a bool the way ASCOM form bodies expect: ``True``/``False``."""
    return "True" if value else "False"


class AlpacaClient:
    """Async client for a single seestar_alp telescope device."""

    def __init__(
        self,
        base_url: str,
        device_num: int = 0,
        *,
        client_id: int = 1,
        timeout_s: float = 30.0,
        provenance: ProvenanceLog | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url
        self.device_num = device_num
        self.client_id = client_id
        self.provenance = provenance

        if client is None:
            self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False

        # Monotonically increasing ClientTransactionID counter (starts at 1).
        self._txn_id = 0

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        provenance: ProvenanceLog | None = None,
    ) -> AlpacaClient:
        """Build an :class:`AlpacaClient` from a :class:`Settings` object."""
        return cls(
            settings.alpaca_base_url,
            settings.alpaca_device_num,
            timeout_s=settings.http_timeout_s,
            provenance=provenance,
        )

    # --- lifecycle ---------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying httpx client, but only if we created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AlpacaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- internals ---------------------------------------------------------

    @property
    def _base_path(self) -> str:
        return f"/api/v1/telescope/{self.device_num}"

    def _next_txn_id(self) -> int:
        self._txn_id += 1
        return self._txn_id

    def _raise_for_http_status(
        self,
        response: httpx.Response,
        verb: str | None,
        *,
        tool: str,
        args: dict,
        request: str | None = None,
        client_txn_id: int | None = None,
    ) -> None:
        """Log provenance and raise :class:`AlpacaError` for a 4xx/5xx response.

        seestar_alp returns an error body like
        ``{"title": "...", "description": "..."}`` on failures; surface that
        (falling back to the raw text) as the error message.
        """
        try:
            data = response.json()
        except Exception:
            data = {}
        self._log(
            tool=tool,
            args=args,
            request=request,
            client_txn_id=client_txn_id,
            response_code=response.status_code,
        )
        message = str(
            data.get("description") or data.get("title") or response.text
        )[:200]
        raise AlpacaError(response.status_code, message, verb)

    def _parse_envelope(self, data: dict, verb: str | None) -> tuple[Any, int, int]:
        """Return ``(value, error_number, server_txn_id)``, raising on errors."""
        error_number = int(data.get("ErrorNumber", 0))
        error_message = data.get("ErrorMessage", "") or ""
        server_txn_id = data.get("ServerTransactionID")
        if error_number != 0:
            if error_number == ERR_NOT_IMPLEMENTED:
                raise AlpacaNotImplemented(error_number, error_message, verb)
            if error_number == ERR_ACTION_NOT_IMPLEMENTED:
                raise AlpacaActionNotImplemented(error_number, error_message, verb)
            raise AlpacaError(error_number, error_message, verb)
        return data.get("Value"), error_number, server_txn_id

    # --- core low-level methods -------------------------------------------

    async def get_property(self, verb: str) -> Any:
        """GET a device property; return its ``Value`` or raise on error."""
        verb = verb.lower()
        client_txn_id = self._next_txn_id()
        params = {
            "ClientID": self.client_id,
            "ClientTransactionID": client_txn_id,
        }
        try:
            response = await self._client.get(
                f"{self._base_path}/{verb}", params=params
            )
        except httpx.RequestError as e:
            self._log(
                tool=f"alpaca.get.{verb}",
                args={"verb": verb},
                client_txn_id=client_txn_id,
                response_code=None,
                note=f"transport error: {type(e).__name__}",
            )
            raise AlpacaTransportError(-1, str(e), verb) from e
        if response.status_code >= 400:
            self._raise_for_http_status(
                response, verb, tool=f"alpaca.get.{verb}", args={"verb": verb},
                client_txn_id=client_txn_id,
            )
        data = response.json()
        server_txn_id = data.get("ServerTransactionID")
        error_number = int(data.get("ErrorNumber", 0))
        self._log(
            tool=f"alpaca.get.{verb}",
            args={"verb": verb},
            client_txn_id=client_txn_id,
            server_txn_id=server_txn_id,
            response_code=error_number,
        )
        value, _, _ = self._parse_envelope(data, verb)
        return value

    async def put_property(self, verb: str, **fields: Any) -> Any:
        """PUT a device property / invoke a standard verb; return ``Value``."""
        verb = verb.lower()
        client_txn_id = self._next_txn_id()
        body = self._form_body(fields, client_txn_id)
        try:
            response = await self._client.put(f"{self._base_path}/{verb}", data=body)
        except httpx.RequestError as e:
            self._log(
                tool=f"alpaca.put.{verb}",
                args={"verb": verb},
                request=_urlencode(body),
                client_txn_id=client_txn_id,
                response_code=None,
                note=f"transport error: {type(e).__name__}",
            )
            raise AlpacaTransportError(-1, str(e), verb) from e
        if response.status_code >= 400:
            self._raise_for_http_status(
                response, verb, tool=f"alpaca.put.{verb}",
                args={"verb": verb}, request=_urlencode(body),
                client_txn_id=client_txn_id,
            )
        data = response.json()
        server_txn_id = data.get("ServerTransactionID")
        error_number = int(data.get("ErrorNumber", 0))
        self._log(
            tool=f"alpaca.put.{verb}",
            args={"verb": verb},
            request=_urlencode(body),
            client_txn_id=client_txn_id,
            server_txn_id=server_txn_id,
            response_code=error_number,
        )
        value, _, _ = self._parse_envelope(data, verb)
        return value

    async def invoke_action(
        self,
        action: str,
        parameters: Any = "",
        *,
        is_async: bool = False,
    ) -> Any:
        """Invoke a seestar_alp action via ``PUT .../action``.

        ``parameters`` is serialized to a JSON *string* for the ``Parameters``
        form field (empty string when falsy/empty). ``action`` is used verbatim
        as the ``Action`` field -- pass ``method_sync`` / ``method_async`` (or
        use the helpers below) to reach native JSON-RPC methods.
        """
        client_txn_id = self._next_txn_id()
        if isinstance(parameters, str):
            parameters_str = parameters
        elif parameters == "" or parameters is None:
            parameters_str = ""
        else:
            parameters_str = json.dumps(parameters)

        fields = {"Action": action, "Parameters": parameters_str}
        body = self._form_body(fields, client_txn_id)
        try:
            response = await self._client.put(f"{self._base_path}/action", data=body)
        except httpx.RequestError as e:
            self._log(
                tool="alpaca.put.action",
                args={"action": action, "is_async": is_async},
                request=_urlencode(body),
                client_txn_id=client_txn_id,
                response_code=None,
                note=f"transport error: {type(e).__name__}",
            )
            raise AlpacaTransportError(-1, str(e), "action") from e
        if response.status_code >= 400:
            self._raise_for_http_status(
                response, "action", tool="alpaca.put.action",
                args={"action": action, "is_async": is_async},
                request=_urlencode(body), client_txn_id=client_txn_id,
            )
        data = response.json()
        server_txn_id = data.get("ServerTransactionID")
        error_number = int(data.get("ErrorNumber", 0))
        self._log(
            tool="alpaca.put.action",
            args={"action": action, "is_async": is_async},
            request=_urlencode(body),
            client_txn_id=client_txn_id,
            server_txn_id=server_txn_id,
            response_code=error_number,
            note=f"action={action}",
        )
        value, _, _ = self._parse_envelope(data, "action")
        return value

    async def method_sync(
        self, method: str, params: list | dict | None = None
    ) -> Any:
        """Call a native JSON-RPC ``method`` synchronously via the tunnel."""
        return await self.invoke_action(
            "method_sync",
            {"method": method, "params": params if params is not None else []},
        )

    async def method_async(
        self, method: str, params: list | dict | None = None
    ) -> Any:
        """Call a native JSON-RPC ``method`` non-blocking via the tunnel."""
        return await self.invoke_action(
            "method_async",
            {"method": method, "params": params if params is not None else []},
            is_async=True,
        )

    # --- form/body helpers -------------------------------------------------

    def _form_body(self, fields: dict[str, Any], client_txn_id: int) -> dict[str, str]:
        """Assemble an ordered form-body dict with ClientID + ClientTransactionID."""
        body: dict[str, str] = {}
        for key, value in fields.items():
            if isinstance(value, bool):
                body[key] = _alpaca_bool(value)
            else:
                body[key] = str(value)
        body["ClientID"] = str(self.client_id)
        body["ClientTransactionID"] = str(client_txn_id)
        return body

    def _log(self, **kwargs: Any) -> None:
        if self.provenance is not None:
            self.provenance.log_call(**kwargs)

    # --- convenience wrappers (thin standard-verb / native helpers) --------

    async def get_connected(self) -> bool:
        return await self.get_property("connected")

    async def set_connected(self, connected: bool) -> Any:
        return await self.put_property("connected", Connected=connected)

    async def get_ra(self) -> float:
        return await self.get_property("rightascension")

    async def get_dec(self) -> float:
        return await self.get_property("declination")

    async def get_tracking(self) -> bool:
        return await self.get_property("tracking")

    async def set_tracking(self, tracking: bool) -> Any:
        return await self.put_property("tracking", Tracking=tracking)

    async def is_slewing(self) -> bool:
        return await self.get_property("slewing")

    async def slew_to_target(self) -> Any:
        return await self.put_property("slewtotarget")

    async def abort_slew(self) -> Any:
        return await self.put_property("abortslew")


def _urlencode(body: dict[str, str]) -> str:
    """Render a form body dict as an ``x-www-form-urlencoded`` string.

    Mirrors what httpx sends on the wire so the provenance log records the
    exact request (which the provenance layer then redacts as needed).
    """
    from urllib.parse import urlencode

    return urlencode(body)
