# Adding a Language

Adding a new debugger backend should touch one runtime extension point:
`debug_service/factory.py`.

1. Add the new language value to `Language` in `debug_service/models.py` so the
   HTTP request model accepts it.
2. Add the adapter import and one `_REGISTRY` entry in `debug_service/factory.py`.

Do not import concrete adapters from `main.py`, `service.py`, or another
adapter. The factory is the only module allowed to know concrete adapter classes.

Verify the boundary with:

```bash
.venv/bin/python -m pytest tests/test_factory.py -q
grep -rE "from \.adapters\.\w+_adapter import" debug_service/ | grep -v factory.py
```
