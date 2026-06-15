# RpaClaw Runtime Adapter Image

This directory contains the local build contract for the AIO session runtime adapter image. The image is the untrusted execution-plane sidecar for one user session; it is not the Host Backend and must not persist product facts such as `AcceptedTrace`, expected signals, generated Skill truth, or artifact ownership.

## Local build

Run from the `RpaClaw` directory so the Docker build context can copy the adapter package and runtime image files:

```powershell
cd .\RpaClaw
docker build -f runtime-adapter/Dockerfile -t rpaclaw-runtime-adapter:dev .
```

The local image installs `runtime-adapter/requirements.txt`, not the full Host Backend dependency set. By default the slim image also installs Debian `chromium` for browser smoke. If the local apt mirror is unstable, build from a browser-ready base image and skip the apt Chromium install:

```powershell
docker build `
  --build-arg BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.57.0-noble `
  --build-arg INSTALL_CHROMIUM=false `
  -f runtime-adapter/Dockerfile `
  -t rpaclaw-runtime-adapter:dev .
```

Browser, Playwright, or inner-network AIO-specific dependencies should be added deliberately when the published adapter image needs them; they should not be pulled in by copying the entire Host Backend runtime.

The image entrypoint starts only the adapter service:

```text
python -m uvicorn backend.runtime.adapter_app:app --host 0.0.0.0 --port 8080
```

It deliberately does not start `backend.main:app`.

## Adapter environment

The adapter process reads only `RUNTIME_ADAPTER_*` settings:

```text
RUNTIME_ADAPTER_WORKSPACE_ROOT=/workspace
RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION=true
RUNTIME_ADAPTER_CDP_URL=ws://127.0.0.1:9222/devtools/browser/<browser-id>
RUNTIME_ADAPTER_BROWSER_VIEW_URL=http://<route>/browser
RUNTIME_ADAPTER_TOKEN=<session-adapter-token>
RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads
RUNTIME_ADAPTER_VERSION=<image-or-git-version>
RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH=true|false
RUNTIME_ADAPTER_BROWSER_EXECUTABLE=<optional chromium/chrome path>
RUNTIME_ADAPTER_BROWSER_DEBUG_PORT=9222
RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL=ws://<host>:<published-cdp-port>
RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_URL=<optional full override>
```

Before publishing or wiring the image into AIO, run:

```powershell
python -m backend.runtime.adapter_app --self-check
```

`--self-check` prints the same sanitized health payload as `/health`. It returns `0` for `status=ok` and `1` for `status=degraded`; it reports only `token_required`, never the token value.

## Containerized browser smoke

For local fake-AIO container validation, publish both the adapter HTTP port and the browser CDP port:

```powershell
docker run --rm -p 18081:8080 -p 19222:9222 `
  -e RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH=true `
  -e RUNTIME_ADAPTER_BROWSER_DEBUG_PORT=9222 `
  -e RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL=ws://127.0.0.1:19222 `
  rpaclaw-runtime-adapter:dev
```

`/v1/browser/info` lazily starts Chromium when browser launch is enabled. The returned CDP URL must be Host-reachable; in local Docker this usually means mapping container port `9222` to a host port and passing `RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL` so the adapter preserves the real `/devtools/browser/<browser-id>` path while replacing the host/port. The adapter also injects a minimal listener probe named `__rpaclawRuntimeAdapterListener` into the launched browser page so the smoke can assert that the execution-plane browser is not merely started, but script-injectable.

## Host/AIO wiring

The Host Backend points real AIO creation at this image through provider settings such as:

```text
AIO_RUNTIME_IMAGE=rpaclaw-runtime-adapter:dev
AIO_RUNTIME_ADAPTER_ENV=RUNTIME_ADAPTER_TOKEN=<session-adapter-token>,RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads,RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION=true
```

The AIO platform remains responsible for sandbox creation, resource isolation, route exposure, TTL, and deletion. The adapter image only exposes the session-scoped semantic API that `RuntimeAdapterClient` calls.

## Local smoke

Use the in-process fake AIO lifecycle smoke before trying the real AIO lifecycle:

```powershell
cd .\RpaClaw
python -m backend.runtime.adapter_smoke --mode aio --workspace-root .runtime-adapter-smoke-aio
```

The smoke output includes `adapter_self_check`, AIO lifecycle payloads with token values sanitized, provider health, and a skill upload/run/download round trip.

After the image is built and Docker Desktop is running, use the local fake AIO container smoke to exercise Host provider create/status/delete against a real adapter container:

```powershell
cd .\RpaClaw
python -m backend.runtime.adapter_smoke --mode aio_container --adapter-token adapter-token
```

`aio_container` starts an in-process local fake AIO lifecycle API, but that API creates and later deletes an actual `rpaclaw-runtime-adapter:dev` container. The smoke verifies adapter `/health`, lazy browser launch through `/v1/browser/info`, and listener injection with marker `__rpaclawRuntimeAdapterListener`. `RuntimeAdapterClient` disables system proxy use for adapter routes so local `127.0.0.1` Docker traffic is not hijacked by host proxy settings.

Once real AIO routing is reachable in the inner network, use the configured real lifecycle smoke:

```powershell
cd .\RpaClaw
python -m backend.runtime.adapter_smoke --mode aio_real --workspace-root .runtime-adapter-smoke-aio-real
```

`aio_real` uses the actual `AIO_RUNTIME_*` environment variables and does not start the in-process fake AIO API.
By default it deletes the AIO sandbox at the end of the smoke. Add `--keep-runtime`
when an inner-network failure needs the sandbox left alive for AIO logs, browser/CDP inspection, or route debugging.

## Health and file policy diagnostics

`GET /health` and `python -m backend.runtime.adapter_app --self-check` share the same sanitized diagnostic shape. A healthy adapter image should report:

```text
status=ok
contract_version=v1
config.token_required=true|false
config.file_policy.max_inline_file_write_bytes=10485760
config.file_policy.max_file_download_bytes=52428800
config.file_policy.oversized_hash_status=skipped_oversized
```

The Host provider copies the non-sensitive `config.file_policy` fields into `SessionRuntimeRecord.metadata.adapter_file_policy`. This lets `/runtime/session/{session_id}/status`, `/runtime/sessions`, and smoke output show which file policy the running adapter image actually exposes without logging tokens or raw artifact contents.

The inline file API is intentionally bounded:

- `/files/write` rejects a single file larger than 10 MiB before creating workspace files.
- `/files/download` rejects a single file larger than 50 MiB before reading it into memory.
- `/rpa/downloads` still lists oversized artifacts, but skips `sha256` and returns `hash_status=skipped_oversized`.
- Host workspace helpers skip pulling those oversized artifacts and return `download_status=skipped_oversized`.

Large-file transfer is a future AIO/object-store/chunked-artifact concern. Do not expand this image by silently raising inline JSON/base64 limits; update `docs/decisions/ADR-005-aio-runtime-adapter-file-api-policy.md` first.

## Inner-network handoff checklist

Use `docs/rpa/aio-runtime-adapter-internal-handoff.md` as the inner-network Agent handoff entry. The checklist below is a compact image-side reminder, not the full integration guide.

Before asking Host/RPA code to change, verify the real AIO integration against this checklist:

- Publish the adapter image and set `RUNTIME_ADAPTER_VERSION` to the image tag or git revision.
- Configure real AIO create/status/delete paths through `AIO_RUNTIME_*` settings.
- Inject adapter-side settings through `AIO_RUNTIME_ADAPTER_ENV`; at minimum align `RUNTIME_ADAPTER_TOKEN`, `RUNTIME_ADAPTER_DOWNLOADS_DIR`, and execution/browser settings.
- Run `python -m backend.runtime.aio_runtime_provider --diagnose --sample-response <real-aio-response.json>` against real create/status samples and confirm the sanitized runtime summary maps `sandbox_id`, `route_base_url`, `browser_view_url`, status, and expiry fields.
- Run `python -m backend.runtime.adapter_smoke --mode aio_real --workspace-root <tmp-dir>` with the real AIO wiring once AIO routing is reachable. Add `--keep-runtime` only when preserving the failing sandbox is needed for debugging.
- Confirm `/health.contract_version` is `v1`, `adapter_self_check.status` is `ok`, `adapter_file_policy` appears in runtime metadata, and token values are absent from smoke/status output.
- Confirm `/v1/browser/info` returns a reachable CDP URL only after the sandbox is ready; `creating` runtimes must still be rejected by Host proxy/CDP connector ready gates.
- Confirm EKS multi-instance Host Backend deployment uses the shared runtime record store so duplicate `ensure_runtime()` calls converge on one session runtime.

Real AIO lifecycle, image registry publication, Chromium/CDP stability, noVNC or browser view routing, and true page interaction smoke remain inner-network acceptance work. The adapter must not own `AcceptedTrace`, Skill truth, artifact ownership, or Harness expected signals.
