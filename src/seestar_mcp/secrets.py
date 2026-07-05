"""Least-privilege secrets loader for seestar-mcp.

Why this module exists
----------------------
The firmware-7.18+ handshake on the Seestar's native control port (:4700) is a
mandatory RSA challenge-response (``get_verify_str`` -> sign challenge ->
``verify_client`` -> ``pi_is_verified``). **Today, ``seestar_alp`` owns that
handshake for us** — this MCP server never opens :4700 directly, so it does not
currently need the RSA private key. This store nonetheless exists so that:

- No secret ever lands in :mod:`seestar_mcp.config` (``Settings``), the
  provenance log, a manifest, or anywhere in source. ``config.py`` holds only
  non-sensitive endpoints/thresholds; credentials live *here*, sourced from the
  environment or a gitignored ``secrets/`` directory (the repo ``.gitignore``
  already ignores ``secrets/``, ``*.pem``, ``*.key``, and ``.env``).
- If we ever DO talk to :4700 directly (e.g. if we stop delegating to
  ``seestar_alp``), this is the single, least-privilege place to hold the key.
- The auth material is updatable in exactly one place. Firmware updates are a
  known, recurring breakage vector for this handshake; keeping the key behind a
  single loader means a firmware bump is a one-line key swap, not a code change.

Security contract: values are read on demand and never cached, never logged,
and never rendered by ``__repr__``/``__str__`` or :meth:`SecretStore.status`.
"""

from __future__ import annotations

from pathlib import Path

# Known secret names this store can report on via ``status()`` (never values).
_KNOWN_SECRETS = ("rsa_private_key",)


class SecretStore:
    """Load secrets from the environment or a gitignored secrets directory.

    Resolution order for :meth:`get` is env var first (``{env_prefix}{NAME}``,
    upper-cased), then a file named ``name`` under ``base_dir``. Missing -> None.
    Nothing is cached; nothing is logged; no value is ever exposed in ``repr``,
    ``str``, or :meth:`status`.
    """

    def __init__(
        self,
        base_dir: Path = Path("./secrets"),
        env_prefix: str = "SEESTAR_SECRET_",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.env_prefix = env_prefix

    # --- generic lookup ---------------------------------------------------

    def get(self, name: str) -> str | None:
        """Return the secret ``name`` from env, else a ``base_dir`` file, else None.

        Env var checked first: ``f"{env_prefix}{name.upper()}"``. Otherwise the
        file ``base_dir/name`` is read and ``.strip()``-ed. Never logs the value.
        """
        import os

        env_value = os.environ.get(f"{self.env_prefix}{name.upper()}")
        if env_value is not None:
            return env_value

        path = self.base_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()

        return None

    def has(self, name: str) -> bool:
        """True if secret ``name`` resolves to a value (env or file)."""
        return self.get(name) is not None

    # --- RSA private key (firmware 7.18+ :4700 handshake) -----------------

    def get_rsa_private_key(self) -> str | None:
        """Return the RSA private key material, or None if not configured.

        Resolution order:
        1. The generic ``rsa_private_key`` secret (env ``SEESTAR_SECRET_RSA_PRIVATE_KEY``
           or file ``base_dir/rsa_private_key``).
        2. A PEM key file whose path is given by env
           ``SEESTAR_SECRET_RSA_KEY_FILE``.
        3. A conventional ``base_dir/rsa_private_key.pem`` file.

        The key is read on demand and never cached or logged.
        """
        import os

        direct = self.get("rsa_private_key")
        if direct is not None:
            return direct

        key_file = os.environ.get(f"{self.env_prefix}RSA_KEY_FILE")
        if key_file:
            path = Path(key_file)
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()

        pem_path = self.base_dir / "rsa_private_key.pem"
        if pem_path.is_file():
            return pem_path.read_text(encoding="utf-8").strip()

        return None

    def has_rsa_key(self) -> bool:
        """True if an RSA private key is configured (env, secret file, or PEM)."""
        return self.get_rsa_private_key() is not None

    # --- safe introspection (never exposes values) ------------------------

    def status(self) -> dict[str, str]:
        """Report ``"present"``/``"absent"`` per known secret — never the value.

        Safe to log: this reveals only whether a secret is configured, never
        any secret material.
        """
        report: dict[str, str] = {}
        for name in _KNOWN_SECRETS:
            if name == "rsa_private_key":
                present = self.has_rsa_key()
            else:
                present = self.has(name)
            report[name] = "present" if present else "absent"
        return report

    def __repr__(self) -> str:
        # Presence only, NEVER the value.
        rsa = "present" if self.has_rsa_key() else "absent"
        return f"SecretStore(base_dir={self.base_dir!s}, rsa_private_key={rsa})"

    __str__ = __repr__
