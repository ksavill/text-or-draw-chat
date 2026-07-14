# Contributing

## Development setup

Use Python 3.11 or newer:

```powershell
python -m pip install -r requirements-dev.txt
python server.py
```

The installed console equivalents are `pictochat-server` and
`pictochat-bridge`.

## Tests

Run the complete suite before submitting a change:

```powershell
python -m pytest
```

Test placement should follow the behavior being exercised:

- `tests/unit/` for pure functions and isolated adapters.
- `tests/integration/` for backend transports and room behavior.
- `tests/e2e/` for multi-component socket flows.

Hardware-independent changes must not require an ESP32 or Nintendo DS to run
the test suite. Physical test results should record board/module details,
firmware commit, serial logs, bridge logs, and DS model.

## Review checklist

- Keep browser, backend, bridge, codec, and firmware responsibilities separate.
- Preserve the `python server.py` compatibility launcher unless a migration is
  documented.
- Add regression coverage for protocol or reconnect changes.
- Treat capture-derived field meanings as assumptions until supported by
  evidence; update `pictochat/ASSUMPTIONS.md` when they change.
- Do not commit generated firmware checkouts, build products, credentials, or
  LAN-specific addresses.
- Do not redistribute the selected third-party firmware without permission;
  its upstream repository currently has no licence file.
