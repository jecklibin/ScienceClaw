# AIO Native Local Smoke Evidence - 2026-06-09

本文记录一次本机 AIO native smoke。它验证了 `RUNTIME_MODE=aio_native` 下 Host Backend 连接 AIO browser/CDP 执行面，并覆盖录制 session、listener 注入、手动 trace、多 tab、自然语言操作、区域选择、脚本生成、AIO runtime 路径脚本测试、Skill 保存和无下载场景不阻塞主链路。

边界说明：本文是 API/CDP 级 smoke evidence，不声明真实内网 AIO `create/status/refresh/delete` 已完成，也不声明 EKS 多实例运行时状态持久化已在内网验收。Runtime Adapter 在当前路线中暂缓，不作为第一阶段上线依赖。

## 环境

- AIO sandbox container: `aio-native-manual`
- AIO image: `enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest`
- AIO port: `127.0.0.1:18090`
- Temporary Host Backend port: `127.0.0.1:8010`
- Backend env:
  - `PYTHONPATH=RpaClaw`
  - `STORAGE_BACKEND=local`
  - `AUTH_PROVIDER=none`
  - `RUNTIME_MODE=aio_native`
  - `AIO_BASE_URL=http://127.0.0.1:18090`
  - `AIO_RUNTIME_SANDBOX_ID=aio-native-manual`

## AIO Browser Info

`GET http://127.0.0.1:18090/v1/browser/info` returned:

```json
{
  "success": true,
  "data": {
    "cdp_url": "ws://127.0.0.1:18090/cdp/devtools/browser/3761c619-1bca-453e-a9f7-00f6772ad82e",
    "vnc_url": "http://127.0.0.1:18090/vnc/index.html",
    "viewport": {
      "width": 1280,
      "height": 1024
    }
  }
}
```

No token or sensitive header was required in this local fixed-sandbox smoke.

## Session A: Core Recording, Generate, Test, Save

### 1. Start RPA Session

Request:

```http
POST http://127.0.0.1:8010/api/v1/rpa/session/start
Content-Type: application/json

{"sandbox_session_id":"aio-native-manual"}
```

Result:

- `status=success`
- `session.id=293f7e98-1feb-417a-a068-d68406783b50`
- `session.sandbox_session_id=aio-native-manual`
- `session.active_tab_id=dcd8c4eb-5c1d-42ce-8769-cf8982b1e993`

This proves Host Backend could create a recording session by connecting to the AIO browser/CDP runtime.

### 2. Navigate AIO Page

Request:

```http
POST /api/v1/rpa/session/293f7e98-1feb-417a-a068-d68406783b50/navigate

{"url":"https://github.com/trending"}
```

Result:

- `status=success`
- active tab URL became `https://github.com/trending`
- tab title returned as `Trending repositories on GitHub today ... GitHub`
- accepted navigation trace was later visible in `/timeline`

Then the same session was navigated to a small `data:text/html` smoke page containing:

- textbox `Name`
- button `Click Me`
- link `Open Popup`

### 3. Listener Injection And Manual Event Capture

Using Playwright connected to AIO CDP, the same AIO page was driven with:

- `fill('#name', 'aio-native-smoke')`
- `click('#go')`
- `click('#popup')`

`GET /api/v1/rpa/session/293f7e98-1feb-417a-a068-d68406783b50/timeline` returned accepted traces including:

- `action=navigate`, `source=manual`, `signals.tab.tab_id=dcd8c4eb-5c1d-42ce-8769-cf8982b1e993`
- `action=fill`, `value=aio-native-smoke`, locator `page.get_by_role("textbox", name="Name")`
- `action=click`, locator `page.get_by_role("button", name="Click Me")`
- `action=click`, locator `page.get_by_role("link", name="Open Popup")`

This proves the existing recorder listener JS was injected into the AIO browser page and that manual click/fill/navigation events entered the accepted timeline.

### 4. Script Generation

Request:

```http
POST /api/v1/rpa/session/293f7e98-1feb-417a-a068-d68406783b50/generate

{"params":{}}
```

Result:

- `status=success`
- generated script contained `execute_skill(page, **kwargs)`
- generated trace steps included navigate, fill, and click operations derived from accepted traces

### 5. Script Test Execution

Request:

```http
POST /api/v1/rpa/session/293f7e98-1feb-417a-a068-d68406783b50/test

{"params":{}}
```

Result:

- `status=success`
- `result.success=true`
- `result.output=SKILL_SUCCESS`
- logs included:
  - `TRACE_DONE 0: Navigate to https://github.com/trending`
  - `TRACE_DONE 1: Navigate to data:text/html...AIO Native Smoke`
  - `TRACE_DONE 2: ... textbox("Name")`
  - `TRACE_DONE 3: ... button("Click Me")`
  - `TRACE_DONE 4: ... link("Open Popup")`

Important detail: although the returned script string includes a standalone `main()` that can launch a browser when run as a local script, the `/test` route executed `execute_skill(page, ...)` through `get_cdp_connector().get_browser(session.sandbox_session_id)`, so this API smoke exercised the AIO runtime browser path.

### 6. Skill Save

Request:

```http
POST /api/v1/rpa/session/293f7e98-1feb-417a-a068-d68406783b50/save

{
  "skill_name": "aio_native_smoke_skill",
  "description": "AIO native smoke skill",
  "params": {}
}
```

Result:

- `status=success`
- `skill_name=aio_native_smoke_skill`
- local generated files were written under `Skills/aio_native_smoke_skill/`
- the generated local Skill directory contained `SKILL.md`, `skill.py`, `params.json`, and `skill.meta.json`

The generated Skill directory was observed during smoke and then cleaned up because it was local output, not a governed regression asset.

### 7. Stop Session

Request:

```http
POST /api/v1/rpa/session/293f7e98-1feb-417a-a068-d68406783b50/stop
```

Result:

- `status=success`
- `session.status=stopped`
- `trace_count=10`
- `diagnostic_count=0`

## Session B: Multi-Tab, Natural Language, Region Selection

Session:

- `session.id=99ba22bc-9dc4-465b-9438-96b899bbde65`
- `session.sandbox_session_id=aio-native-manual`
- main tab: `da8eb037-7764-49a9-a017-d18d52b7feb7`
- popup tab: `070764dc-a6ab-49ba-bb55-0ca0a38a161e`

### 1. Multi-Tab

The main tab was navigated to `AIO Multi Tab Smoke`, a `data:text/html` page with:

- link `Open Example`, `target=_blank`, `href=https://example.com/`
- button `First Tab Button`

Playwright connected to AIO CDP clicked `#open-example`.

External CDP observation:

```json
{
  "before_pages": 3,
  "after_pages": 4,
  "urls": ["...", "https://example.com/"],
  "titles": ["...", "Example Domain"]
}
```

Backend `/tabs` after the click returned:

- main tab `da8eb037-7764-49a9-a017-d18d52b7feb7`, title `AIO Multi Tab Smoke`, active `false`
- new tab `070764dc-a6ab-49ba-bb55-0ca0a38a161e`, title `Example Domain`, url `https://example.com/`, opener tab `da8eb037-7764-49a9-a017-d18d52b7feb7`, active `true`

Timeline evidence:

- `trace-a762e2ce-cc80-4d24-a1bf-0bb0771e94ac`: manual click trace for link `Open Example`
- `signals.popup.source_tab_id=da8eb037-7764-49a9-a017-d18d52b7feb7`
- `signals.popup.target_tab_id=070764dc-a6ab-49ba-bb55-0ca0a38a161e`

Then the first tab was activated:

```http
POST /api/v1/rpa/session/99ba22bc-9dc4-465b-9438-96b899bbde65/tabs/da8eb037-7764-49a9-a017-d18d52b7feb7/activate
```

Timeline evidence:

- `trace-545fb263-bda5-459d-aab6-eb6cc5a48f48`: `action=switch_tab`
- source tab `070764dc-a6ab-49ba-bb55-0ca0a38a161e`
- target tab `da8eb037-7764-49a9-a017-d18d52b7feb7`

After switching back to the main tab, Playwright clicked `#first`. Timeline evidence:

- `trace-85ab7c30-7598-472b-94ae-17b56b4c13c3`: manual click trace for `First Tab Button`
- `signals.tab.tab_id=da8eb037-7764-49a9-a017-d18d52b7feb7`

This proves opening a new tab, switching tabs, URL/title/page attribution, and avoiding stale-tab attribution.

### 2. Natural Language Operations

Natural language commands were sent through:

```powershell
'{"message":"...","mode":"chat"}' |
  curl.exe -N -s -X POST "http://127.0.0.1:8010/api/v1/rpa/session/99ba22bc-9dc4-465b-9438-96b899bbde65/chat" `
    -H "Content-Type: application/json" --data-binary "@-"
```

Read page information:

- user message: `读取当前页面标题`
- trace id: `trace-f15379507b2647129cd52da484c728b8`
- source: `ai`
- accepted: `true`
- output included `page_title=AIO Multi Tab Smoke`, `button_label=First Tab Button`, `link_label=Open Example`

Fill:

- user message: `Fill the Name textbox with aio native nl`
- trace id: `trace-6e7e50ca05604b1d8c21d85ead07b03c`
- description: `Fill the Name textbox with 'aio native nl'`
- output: `{"action_performed": true, "action_type": "fill", "filled_value": "aio native nl"}`
- source: `ai`
- accepted: `true`

Click:

- user message: `Click the Submit Name button`
- trace id: `trace-0b8146673b1a4ecc81c2ab88c172774b`
- description: `Click Submit Name button`
- output: `{"action_performed": true, "action_type": "click"}`
- source: `ai`
- accepted: `true`

Navigate:

- user message: `Navigate to https://example.com/`
- trace id: `trace-6dad5e9e689a488eb1162185802252fc`
- after page URL: `https://example.com/`
- after page title: `Example Domain`
- output: `{"action_performed": true, "action_type": "navigate", "url": "https://example.com/"}`
- output key: `navigation_result`
- source: `ai`
- accepted: `true`

This proves natural language read/fill/click/navigate can operate on the AIO page and emit accepted traces.

### 3. Region Selection

The main tab was navigated to `AIO Region Smoke`, a `data:text/html` page with a list:

- `First project`
- `Second project`
- `Third project`

CDP-computed geometry:

```json
{
  "rect": {"x": 28, "y": 79.875, "width": 382, "height": 85},
  "second": {"x": 39, "y": 111.875, "width": 104.921875, "height": 21},
  "viewport": {"width": 1280, "height": 1024}
}
```

`POST /region/element-bounds` at the center of the second button returned:

```json
{
  "rect": {"x": 39.0, "y": 111.875, "width": 104.921875, "height": 21.0},
  "tag": "button",
  "role": "",
  "name": "Second project",
  "text": "Second project",
  "warnings": []
}
```

`POST /region/analyze` for the list rect returned:

- `region_id=region-29dd1fc3382d4f5c92219282d11bc180`
- `inferred_kind=list_region`
- page title `AIO Region Smoke`
- dominant container `ul`
- local text included `First project`, `Second project`, `Third project`

An initial Chinese command `点击选中区域里的第二个项目` with that region id produced an accepted trace but interpreted the action as extraction rather than a click:

- trace id: `trace-7326a5e4ca3340a8a015b4804407bbf8`
- output: `["First project", "Second project", "Third project"]`
- trace contained non-empty `region_context` and `region_scope`

Because successful region commands clear the pending context by design, the same region id was unavailable for a second command. The list region was re-analyzed, returning:

- `region_id=region-49015dadf18a4fc28319f75c87977037`
- `inferred_kind=list_region`

Then this command was sent:

```json
{
  "message": "Click the Second project button inside the selected region",
  "mode": "chat",
  "region_id": "region-49015dadf18a4fc28319f75c87977037"
}
```

Result:

- SSE emitted `event: region_context`
- `trace_added` id: `trace-e24ca45bff3d4fcfb288c75faaef4c05`
- description: `Click the Second project button`
- output: `{"action_performed": true, "action_type": "click", "target": "Second project"}`
- trace contained non-empty `signals.region_selection`
- trace contained non-empty `region_context`
- trace contained non-empty `region_scope`
- accepted: `true`

CDP verification after the trace:

```json
{
  "choice": "second",
  "title": "AIO Region Smoke"
}
```

This proves `element-bounds` / `region/analyze` can return region context, and a region-scoped natural-language operation can execute against the selected area on the AIO browser page while retaining region evidence in the accepted trace.

## Session C: Frontend Recorder Fresh Smoke

This smoke used a temporary, isolated local stack:

- Host Backend: `http://127.0.0.1:8010`
- Frontend: `http://127.0.0.1:5174`
- Frontend env:
  - `VITE_API_URL=http://127.0.0.1:8010`
  - `BACKEND_URL=http://127.0.0.1:8010`
- Backend env:
  - `RUNTIME_MODE=aio_native`
  - `AIO_BASE_URL=http://127.0.0.1:18090`
  - `AIO_RUNTIME_SANDBOX_ID=aio-native-manual`

The first attempt used only `BACKEND_URL` and hit the Vite default proxy target, producing dev-proxy `ECONNREFUSED` / 500 responses for `/api/v1/auth/status`. Restarting the frontend with `VITE_API_URL=http://127.0.0.1:8010` made the browser call the intended backend and all startup requests returned 200.

### 1. Frontend Recording Entry

Opening `http://127.0.0.1:5174/rpa/recorder` produced these frontend-observed API responses:

- `GET /api/v1/client-config`: 200
- `GET /api/v1/auth/status`: 200
- `GET /api/v1/models`: 200
- `GET /api/v1/rpa/harness/config`: 200
- `POST /api/v1/rpa/session/start`: 200

The recorder page entered the recording state:

- UI text included `正在录制`
- step timeline included `Environment ready`
- browser address/tab showed `about:blank`
- region selection button was enabled

Captured `POST /rpa/session/start` response:

- `session_id=dc96a60e-be71-4d7a-ab52-5493e833a8ce`
- `start_status=200`
- `start_sandbox_session_id=rpa-e1a9138a-291f-4b76-ab86-fb269b52eeba`
- `active_tab_id=2154c9b2-093a-43a0-a2b1-093cd8c6bb23`

Although the frontend-generated `sandbox_session_id` is a fresh `rpa-*` id, the backend process was running in `aio_native` mode with `AIO_RUNTIME_SANDBOX_ID=aio-native-manual`, so the browser execution surface was still the fixed local AIO sandbox.

### 2. Frontend Browser Canvas

The frontend rendered the CDP screencast canvas:

```json
{
  "width": 1280,
  "height": 937,
  "clientWidth": 758,
  "clientHeight": 830
}
```

The address bar was filled through the frontend UI with a `data:text/html` page titled `AIO Frontend Region Smoke`, then submitted with Enter. The frontend observed:

- `POST /api/v1/rpa/session/dc96a60e-be71-4d7a-ab52-5493e833a8ce/navigate`: 200
- `navigate_result_status=success`
- timeline displayed a second accepted navigation step
- the browser tab label changed to `AIO Frontend Region Smoke`

This proves the frontend recorder can create a session, render the AIO browser canvas, and navigate the AIO page through the normal UI path.

### 3. Frontend Region Selection Entry

After navigation, the frontend region selection button was present and enabled:

```json
{
  "title": "点击元素或拖拽框选区域 · Esc 取消",
  "aria": "选择页面区域",
  "disabled": false
}
```

Clicking the button in the frontend changed the canvas class to:

```text
w-full h-full object-contain cursor-crosshair
```

The page text included the selection hint `点击元素或拖拽框选区域 · Esc 取消`.

An initial automated drag failed to produce `/region/analyze` because the Playwright coordinates were sent to the top letterbox area of the `object-contain` canvas, not to the rendered browser content. The canvas client box was `758x830`, while the screencast backing size was `1280x937`; the contained browser image therefore started about `138px` below the top of the canvas. After correcting the coordinate mapping from browser viewport coordinates to the contained canvas content rect, the frontend drag selection passed.

Corrected frontend drag smoke:

- `session_id=dd5efb43-0ba8-4c1d-a31d-2c583a361315`
- canvas box: `x=341`, `y=153`, `width=758`, `height=830`
- canvas backing size: `width=1280`, `height=937`
- browser viewport region selected: approximately `x=24..430`, `y=76..180`
- mapped client drag:
  - start: `x=355.2125`, `y=335.5664`
  - end: `x=595.6406`, `y=397.1539`

The frontend observed:

- `POST /api/v1/rpa/session/43992d7b-4260-4801-9322-bf9fc3c8c14d/region/element-bounds`: 200
- `POST /api/v1/rpa/session/dd5efb43-0ba8-4c1d-a31d-2c583a361315/region/analyze`: 200

The frontend `region/analyze` response returned:

```json
{
  "region_id": "region-88d046bba2f84a26a0824486cdb2f4f3",
  "inferred_kind": "list_region",
  "title": "AIO Frontend Correct Region Smoke",
  "local_text": [
    "First projectSecond projectThird project",
    "First project",
    "Second project",
    "Third project"
  ]
}
```

This proves the frontend can enter region selection mode, map user drag coordinates through the screencast canvas, and call the backend `region/analyze` endpoint against the AIO browser page. The temporary session was stopped after the run.

All temporary frontend smoke RPA sessions were stopped after the run, and the temporary 8010/5174 services were shut down.

## Checklist Status

| Goal item | Status from local smoke | Evidence |
| --- | --- | --- |
| AIO execution surface | Passed for local fixed AIO sandbox | AIO container healthy; `/v1/browser/info`; `/session/start` success |
| Recording skill starts | Passed at API and frontend entry level | `/session/start` returned session and active tab; frontend recorder reached `正在录制` |
| Browser view accessible | Passed for frontend CDP canvas | AIO `vnc_url` returned; frontend canvas rendered with `1280x937` backing size and `758x830` client size |
| Listener JS injection | Passed | CDP-driven fill/click produced accepted manual traces |
| Manual click/input/navigation | Passed | accepted navigate/fill/click traces |
| Multi tab | Passed | popup tab opened, `/tabs` attribution correct, switch trace and post-switch click trace recorded on main tab |
| Natural language operations | Passed | accepted AI traces for read, fill, click, and navigate |
| Region selection | Passed | API/CDP evidence: element bounds, region analyze, region-scoped click trace, `region_context` / `region_scope`, page state `choice=second`; frontend evidence: selection button/crosshair, `element-bounds`, and corrected drag-to-`region/analyze` passed |
| Trace -> script generation | Passed | `/generate` returned success |
| Script execution in AIO runtime path | Passed via `/test` route | `/test` used runtime CDP browser and returned `SKILL_SUCCESS` |
| Skill save | Passed | `/save` returned `skill_name=aio_native_smoke_skill`; generated `SKILL.md`, `skill.py`, `params.json`, and `skill.meta.json` were observed before cleanup |
| Downloads/files not blocking | Passed for no-download scenario | no download generated; main chain did not fail |
| Internal handoff | Documented | `docs/rpa/aio-native-internal-handoff.md` and `docs/rpa/aio-native-functional-smoke-checklist.md` |

## Remaining Intranet Handoff Gaps

The local/external-network smoke intentionally excludes the real intranet AIO control plane and EKS deployment because those services are only available after the code is synchronized internally. The remaining handoff items for the internal Agent are:

1. Real intranet lifecycle remains to be adapted and smoked:
   - `POST /api/livefunction/sandboxes`
   - `GET /api/livefunction/sandboxes/{sandboxId}`
   - `POST /api/livefunction/sandboxes/refresh/{sandboxId}`
   - `DELETE /api/livefunction/sandboxes/{sandboxId}`
2. Intranet EKS multi-instance deployment must verify runtime record persistence, idempotent lifecycle operations, and request routing behavior.
3. Frontend visual recording flow now has fresh smoke evidence for session start, canvas rendering, navigation, region-selection entry, `element-bounds`, and corrected drag-to-`region/analyze`.
4. Download/file artifact round trip is only proven as non-blocking for a no-download scenario. A real download scenario can remain out of scope for first smoke unless the target internal flow depends on it.
