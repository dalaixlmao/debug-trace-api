# DebugTrace API

DebugTrace API is a FastAPI service that accepts source code in a supported
language and returns a line-by-line execution trace with local variables.

It currently supports:

- Python via `sys.settrace`
- Go via Delve
- C++ via LLDB
- Java via JDWP/JDI
- JavaScript via the Node/V8 inspector

## Quick Start

The recommended way to run the service is Docker Compose:

```bash
docker compose up --build
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Stop the server with:

```bash
docker compose down
```

## API

### `POST /debug`

Request body:

```json
{
  "language": "python",
  "code": "x = 1\nprint(x)"
}
```

Supported `language` values:

```text
python, go, cpp, java, javascript
```

Success response:

```json
[
  {
    "line": 1,
    "variables": {}
  },
  {
    "line": 2,
    "variables": {
      "x": 1
    }
  }
]
```

Common error responses:

- `400` for unsupported languages or empty code.
- `408` when a debug session times out.
- `422` when submitted code fails to compile.
- `500` when an adapter fails unexpectedly.

## Curl Examples

Python:

```bash
curl --location 'http://127.0.0.1:8000/debug' \
  --header 'Content-Type: application/json' \
  --data '{
    "language": "python",
    "code": "x = 1\ny = 2\nprint(x + y)"
  }'
```

JavaScript:

```bash
curl --location 'http://127.0.0.1:8000/debug' \
  --header 'Content-Type: application/json' \
  --data '{
    "language": "javascript",
    "code": "let m = new Map();\nm.set(\"k\", 1);\nlet s = new Set([1, 2, 3]);\nconsole.log(m, s);"
  }'
```

Go:

```bash
curl --location 'http://127.0.0.1:8000/debug' \
  --header 'Content-Type: application/json' \
  --data '{
    "language": "go",
    "code": "package main\nimport \"fmt\"\nfunc main() {\n    x := 5\n    nums := []int{1, 2, 3}\n    fmt.Println(x, nums)\n}"
  }'
```

Java:

```bash
curl --location 'http://127.0.0.1:8000/debug' \
  --header 'Content-Type: application/json' \
  --data '{
    "language": "java",
    "code": "import java.util.*;\npublic class Main {\n  public static void main(String[] args) {\n    int x = 5;\n    ArrayList<Integer> list = new ArrayList<>();\n    list.add(1);\n    list.add(2);\n    System.out.println(x + list.size());\n  }\n}"
  }'
```

C++:

```bash
curl --location 'http://127.0.0.1:8000/debug' \
  --header 'Content-Type: application/json' \
  --data '{
    "language": "cpp",
    "code": "#include <iostream>\n#include <vector>\nint main() {\n    int x = 5;\n    std::vector<int> nums = {1, 2, 3};\n    std::cout << x + nums.size() << std::endl;\n    return 0;\n}"
  }'
```

A Postman collection is available at:

```text
postman_collection.json
```

## Local Development

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

Run the server locally:

```bash
.venv/bin/python -m uvicorn debug_service.main:app --host 127.0.0.1 --port 8000
```

Run tests:

```bash
.venv/bin/python -m pytest -q
```

Some adapter tests skip automatically when their external toolchain is not
available. Full local language support may require:

- `node`
- `go`
- `dlv`
- `java` and `javac`
- `clang++`
- LLDB Python bindings

The Docker image installs these runtime dependencies for the containerized
server.

## Architecture

The service keeps the HTTP layer thin:

- `debug_service/main.py` exposes `POST /debug`.
- `debug_service/service.py` coordinates a debug session.
- `debug_service/factory.py` resolves a language to an adapter.
- `debug_service/adapters/` contains one strategy per language.
- `debug_service/session.py`, `decorators.py`, and `observers.py` provide
  lifecycle, timeout, validation, and event hooks.

Adding a new language should use the factory as the extension point. See:

```text
ADDING_A_LANGUAGE.md
```

## Docker Notes

The Docker Compose service grants debugger permissions required by LLDB:

- `SYS_PTRACE`
- `seccomp=unconfined`

These are needed so the C++ adapter can debug child processes inside the
container. The image also includes Debian LLDB compatibility symlinks needed by
the Python LLDB bindings.
