---
title: RpaClaw 模型认证能力整体设计
date: 2026-04-30
tags:
  - RpaClaw
  - 模型配置
  - 凭据管理
  - 企业认证
---

# RpaClaw 模型认证能力整体设计

## 1. 业务背景

RpaClaw 在公司内部落地后，模型调用已经不再只是一个“技术配置项”，而是很多业务能力的基础依赖。

用户对话、RPA 录制辅助、页面理解、任务拆解、流程生成、技能运行时决策、定时任务解析等能力，最终都依赖系统能稳定地调用大模型服务。

个人使用场景里，模型接入通常比较简单：

```text
填一个 Base URL
填一个 API Key
选择一个 Model Name
保存后直接调用
```

但是在公司内部环境里，模型服务往往不是直接裸露给业务系统调用的。为了统一安全、权限、审计、成本控制和流量管理，公司通常会在模型服务前面加一层模型网关、API 网关，通常会使用 iam 等验证。

因此，当前只支持 `api_key` 的模型配置方式，在企业场景下会遇到明显瓶颈。

### 1.1 当前业务痛点

**用户配置后仍然可能调用失败**

用户可能已经填好了模型地址和 API Key，但模型网关还要求额外 Header，例如：

```http
Authorization: Bearer company-token
X-Company-Token: abc123
X-Tenant-Id: tenant-a
```

用户会看到“模型配置完成”，但实际对话、录制或任务运行时仍然失败，这时如果是 it 人员写一个 proxy 就能解决问题，但是 RpaClaw 面向无 it 背景的业务人员，不可能让他们写 proxy，于是导致用户体验极差。

### 1.2 功能设计目标

为 RpaClaw 建立一套企业级模型认证能力。

它需要同时解决两个层面的问题：

```text
管理面：
用户如何简单、清晰、安全地配置模型认证。

运行面：
系统如何在真正调用模型时，正确获取、解析、刷新并注入认证信息。
```

最终希望达到的业务效果：

```text
非 it 用户可以自助接入更多公司内部模型服务
认证失败时错误更容易定位
敏感凭据不明文暴露
涉及大模型调用的场景（对话，创建技能等）使用同一套模型认证逻辑
```

## 2. 整体设计原则

### 2.1 模型配置和认证凭据分离

模型配置负责描述：

```text
调用哪个模型
模型服务地址是什么
当前模型绑定哪一条模型认证凭据
```

凭据管理负责保存真实认证配置和敏感数据：

```text
静态 Header / Query / Body 模板
动态 token 的 token_request / inject / variables
真实 token
client secret
app key
账号密码
其他敏感值
```

`ModelConfig` 不应该保存明文 token，也不应该保存完整认证规则。它只保存 `auth_credential_id`，指向一条 `credentials.kind = model_auth` 的凭据。

### 2.2 管理面对用户友好

管理面不要求用户理解底层模板语法，例如：

```text
{{ variables.client_secret }}
{{ variables.tenant_id }}
```

用户应该看到的是表格、JSON 输入、凭据选择、token 字段路径、注入方式等清晰概念。

模板语法是系统内部表达方式，用于把“配置”和“凭据字段”连接起来。

### 2.3 静态和动态认证最终统一成一种运行时结果

无论用户配置的是静态 Header，还是动态 token，底层模型客户端最终只需要知道：

```text
api_key 是什么
headers 是什么
query 是什么
body 里是否需要附加认证字段
```

因此运行时统一解析成：

```python
ResolvedModelAuth(
    api_key="...",
    default_headers={...},
    default_query={...},
    default_body={...},
)
```

底层模型调用不需要知道 token 是静态输入的，还是动态换来的。

## 3. 管理面用户如何使用

### 3.1 模型配置页增加“认证配置”

认证配置仍然放在模型配置页，这样做的好处是用户无需先跳到“凭据管理”手动创建复杂对象，降低理解成本。

用户新增或编辑模型时，页面可以分成两块：

```text
基础配置
- 模型名称
- Provider
- Base URL
- Model Name
- Context Window

认证配置
- 无认证
- API Key
- 静态 Header
- 动态 Token
- 选择已有模型认证凭据
```

`API Key` 不再作为单独的运行时认证 `type`。在数据结构中，它被表达为 `static_headers` 的默认 Authorization 模板：

```json
{
  "type": "static_headers",
  "static_headers": {
    "headers": {
      "Authorization": "Bearer {{ variables.api_key }}"
    },
    "query": {},
    "body": {}
  },
  "variables": {
    "api_key": {
      "value": "sk-xxx",
      "sensitive": true
    }
  }
}
```

管理面可以继续保留“API Key”输入体验，用于兼容用户心智；保存时创建或更新一条 `kind = model_auth` 的凭据，并把 API Key 放入统一 `variables` 字段池。

模型自身只保存：

```text
auth_credential_id = 对应 model_auth 凭据 id
```

### 3.2 无额外认证

这是默认模式。

用户只配置现有字段：

```text
Base URL
Model Name
```

保存后模型的 `auth_credential_id` 为空，运行时不额外注入 Header、Query 或 Body。

适用场景：

```text
不需要任何认证的内部测试模型
认证已经由网络层或代理层处理的模型服务
```

如果用户填写 API Key，则不再属于“无额外认证”，而是保存为 `static_headers` 的默认 Authorization 模板。

### 3.3 静态 Header

静态 Header 用于固定 token、固定租户标识、标准 API Key 等场景。

用户选择“API Key”时，系统默认生成：

```text
Authorization: Bearer {{ variables.api_key }}
```

用户选择“静态 Header”后，管理面提供两种输入方式。

#### 表格模式

适合普通用户：

```text
Header 名称              Header 值                  是否敏感
Authorization            Bearer xxx                 是
X-Company-Token          abc123                     是
X-Tenant-Id              tenant-a                   否
```

用户可以动态添加、删除 Header 行。

每一行至少包含：

```text
Header 名称
Header 值
是否敏感
```

保存时，每个 Header 值进入同一条模型认证凭据的 `variables` 字段池，字段级 `sensitive` 决定是否加密保存。

#### JSON 模式

适合高级用户或从内部文档复制配置：

```json
{
  "Authorization": "Bearer {{ variables.company_token }}",
  "X-Company-Token": "{{ variables.company_token }}",
  "X-Tenant-Id": "{{ variables.tenant_id }}"
}
```

JSON 模式要求：

```text
必须是 JSON object
key 必须是非空字符串
value 必须能转成字符串
不接受数组、字符串、数字作为根节点
引用的 variables 字段必须存在，或在保存时引导用户补齐
```

导入成功后，可以转换回表格展示。

#### 保存后的用户感知

用户以为自己保存的是：

```text
模型请求需要带这些 Header
```

系统实际做的是：

```text
1. 创建或更新一条 kind = model_auth 的 Credential
2. 把认证方式保存到 credential.model_auth.type
3. 把 Header 模板保存到 credential.model_auth.static_headers.headers
4. 把 Header 引用到的字段保存到 credential.model_auth.variables
5. 根据 variables.*.sensitive 决定字段值是否加密
6. ModelConfig 只保存 auth_credential_id 引用
7. 编辑模型时不回显 sensitive=true 的真实值，只显示“已配置”
```

编辑时建议展示：

```text
Header 名称              Header 值
Authorization            已配置
X-Company-Token          已配置
X-Tenant-Id              tenant-a
```

用户可以替换 Header 值，也可以删除 Header。

### 3.4 动态 Token

动态 Token 用于“调用模型前先换 token”的场景。

用户心智是：

```text
系统调用模型前，先去认证服务拿一个临时 token。
拿到 token 后，再把 token 放进模型请求里。
```

动态 token 的 `token_request`、`inject`、`variables` 都持久化在 `credentials.kind = model_auth` 的 `model_auth` 字段中，模型本身仍然只保存 `auth_credential_id`。

管理面建议分两层。

#### 简单模式：OAuth2 Client Credentials

适合常见企业认证服务：

```text
认证方式：OAuth2 Client Credentials
Token URL: https://auth.company.com/oauth/token
Method: POST
Client ID: 填写或选择变量字段
Client Secret: 填写或选择变量字段
Scope: 可选
测试后响应字段池: $.access_token / $.data.access_token 等
注入方式: Authorization: Bearer {$.access_token}
```

这里的 `Client ID` 和 `Client Secret` 不直接写进模型配置。它们保存到模型认证凭据的 `variables` 字段池中，例如：

```json
{
  "client_id": {
    "value": "corp-client",
    "sensitive": false
  },
  "client_secret": {
    "value": "secret-value",
    "sensitive": true
  }
}
```

用户可以在模型配置页里顺手新建模型认证凭据，也可以选择已有凭据。

#### 高级模式：自定义 Token 请求 JSON

适合内部复杂认证：

```json
{
  "type": "dynamic_token",
  "token_request": {
    "method": "POST",
    "url": "https://auth.company.com/token",
    "headers": {
      "Content-Type": "application/json",
      "X-App-Key": "{{ variables.app_key }}"
    },
    "query": {},
    "body_type": "json",
    "body": {
      "client_id": "{{ variables.client_id }}",
      "client_secret": "{{ variables.client_secret }}"
    }
  },
  "inject": {
    "headers": {
      "Authorization": "Bearer {$.data.access_token}"
    },
    "query": {},
    "body": {}
  },
  "variables": {
    "client_id": {
      "value": "client_id",
      "sensitive": false
    },
    "client_secret": {
      "value": "client_secret",
      "sensitive": true
    },
    "app_key": {
      "value": "app_key",
      "sensitive": true
    }
  }
}
```

高级模式允许用户描述：

```text
如何请求 token
请求 token 时需要哪些 headers/query/body
body 按 json/form/raw 哪种方式发送
响应里的 token 在哪个字段
响应里的过期时间在哪个字段
拿到 token 后注入到模型请求的哪里
需要哪些变量字段
每个变量字段是否敏感
```

动态 Token 管理面同时提供统一“变量字段池”表格：

```text
字段名              字段值                 是否敏感
client_id           client_id              否
client_secret       已配置                 是
app_key             已配置                 是
tenant_id           tenant-a               否
```

JSON 中可以在 `token_request.url`、`headers`、`query`、`body` 的任意位置引用这些变量：

```text
{{ variables.client_id }}
{{ variables.client_secret }}
{{ variables.tenant_id }}
```

保存时不再维护单独的 `credentials` 引用列表。真实值统一进入当前 `model_auth` 凭据的 `variables` 字段池，字段级 `sensitive` 决定是否加密。

#### 动态 Token 的编辑体验

编辑动态 Token 配置时：

```text
凭据 name/description 可以编辑
Token URL 可以回显
method 可以回显
Token 请求 headers/query/body 可以回显
测试响应字段池用于配置 inject 规则
inject 规则可以回显
sensitive=false 的变量值可以回显
sensitive=true 的变量值不回显，只显示“已配置”
```

用户可以重新选择已有模型认证凭据，也可以在当前凭据上修改 name、description 和配置。

### 3.5 模型页临时新建认证

用户在模型页临时新建认证时，系统自动生成凭据名称和描述，避免用户被迫理解“凭据对象”的概念。

命名规则：

```text
name = "{模型显示名称} 认证"
description = "由模型「{模型显示名称}」创建，类型：{认证类型中文名}"
```

如果同一用户下出现重名，则追加序号：

```text
Claude 网关 认证
Claude 网关 认证 2
Claude 网关 认证 3
```

认证类型中文名示例：

```text
API Key
静态 Header
动态 Token
OAuth2 Client Credentials
```

模型页临时创建的认证不是一次性对象。创建后它会进入凭据管理页面，用户可以在那里继续编辑 `name`、`description` 和完整配置。

## 4. 运行面整体链路

运行面核心链路：

```mermaid
flowchart LR
  A["业务入口：对话 / RPA / 任务 / 测试连接"] --> B["ModelConfig"]
  B --> C["auth_credential_id"]
  C --> D["Credential kind=model_auth"]
  D --> E["ModelAuthResolver"]
  E --> F["Credential Vault"]
  E --> G["Token Cache"]
  E --> H["ResolvedModelAuth"]
  H --> I["_SafeChatOpenAI / Gemini / 其他模型客户端"]
  I --> J["模型服务请求"]
```

职责划分：

| 对象 | 职责 |
| --- | --- |
| `ModelConfig` | 持久化模型基础信息和 `auth_credential_id` |
| `auth_credential_id` | 指向一条 `kind = model_auth` 的凭据 |
| `Credential Vault` | 保存 `kind = basic` 和 `kind = model_auth` 凭据，并按字段加解密敏感值 |
| `credential.model_auth` | 描述模型认证规则，包含 `type`、`static_headers`、`token_request`、`inject`、`variables` |
| `ModelAuthResolver` | 运行时把模型认证凭据解析成真实可用的认证参数 |
| `Token Cache` | 缓存动态 token，避免每次调用都请求认证中心 |
| `ResolvedModelAuth` | 运行时临时结果，包含最终 api_key、headers、query、body 扩展 |
| 模型客户端 | 使用最终认证结果发起模型调用 |

## 5. 静态 Token 运行流程

用户在管理面配置：

```json
{
  "Authorization": "Bearer company-token",
  "X-Tenant": "tenant-a"
}
```

保存后，模型配置里不保存明文 token，也不保存 Header 模板，而是保存：

```json
{
  "auth_credential_id": "cred_model_auth_static"
}
```

对应的 `credentials.kind = model_auth` 凭据保存：

```json
{
  "id": "cred_model_auth_static",
  "kind": "model_auth",
  "name": "Claude 网关 认证",
  "description": "由模型「Claude 网关」创建，类型：静态 Header",
  "model_auth": {
    "version": 2,
    "type": "static_headers",
    "static_headers": {
      "headers": {
        "Authorization": "{{ variables.company_token }}",
        "X-Tenant": "{{ variables.tenant_id }}"
      },
      "query": {},
      "body": {}
    },
    "variables": {
      "company_token": {
        "value": "Bearer company-token",
        "sensitive": true
      },
      "tenant_id": {
        "value": "tenant-a",
        "sensitive": false
      }
    }
  }
}
```

API Key 场景也是同一套 `static_headers`：

```json
{
  "type": "static_headers",
  "static_headers": {
    "headers": {
      "Authorization": "Bearer {{ variables.api_key }}"
    },
    "query": {},
    "body": {}
  },
  "variables": {
    "api_key": {
      "value": "sk-xxx",
      "sensitive": true
    }
  }
}
```

实际调用时：

```text
1. 业务代码拿到 ModelConfig
2. ModelAuthResolver 读取 auth_credential_id
3. 根据 auth_credential_id 加载 kind = model_auth 的凭据
4. 发现 model_auth.type = static_headers
5. 从 model_auth.variables 中读取字段值
6. 对 sensitive=true 的字段执行解密
7. 渲染 static_headers.headers/query/body 模板
8. 生成 ResolvedModelAuth
9. 构造模型客户端时注入 default_headers/default_query/default_body
10. 模型请求自动带上这些认证参数
```

运行时结果：

```python
ResolvedModelAuth(
    api_key=None,
    default_headers={
        "Authorization": "Bearer company-token",
        "X-Tenant": "tenant-a",
    },
    default_query={},
    default_body={},
)
```

最终模型请求：

```http
POST /chat/completions
Authorization: Bearer company-token
X-Tenant: tenant-a
```

## 6. 动态 Token 运行流程

模型配置只保存：

```json
{
  "auth_credential_id": "cred_model_auth_dynamic"
}
```

动态 token 配置持久化在模型认证凭据中：

```json
{
  "id": "cred_model_auth_dynamic",
  "kind": "model_auth",
  "name": "公司模型 OAuth 认证",
  "description": "由模型「公司模型」创建，类型：动态 Token",
  "model_auth": {
    "version": 2,
    "type": "dynamic_token",
    "token_request": {
      "method": "POST",
      "url": "https://auth.company.com/token",
      "headers": {
        "Content-Type": "application/json"
      },
      "query": {},
      "body_type": "json",
      "body": {
        "client_id": "{{ variables.client_id }}",
        "client_secret": "{{ variables.client_secret }}"
      }
    },
    "inject": {
      "headers": {
        "Authorization": "Bearer {$.access_token}"
      },
      "query": {},
      "body": {}
    },
    "variables": {
      "client_id": {
        "value": "corp-client",
        "sensitive": false
      },
      "client_secret": {
        "value": "secret-value",
        "sensitive": true
      }
    }
  }
}
```

实际调用时：

```text
1. ModelAuthResolver 读取 ModelConfig.auth_credential_id
2. 加载 kind = model_auth 的凭据
3. 发现 model_auth.type = dynamic_token
4. 根据 user_id + auth_credential_id + model_auth_hash 检查内存 token cache
5. 如果 token 存在且未过期，直接复用
6. 如果 token 不存在或即将过期，准备 token_request
7. 从 model_auth.variables 解密 client_secret/app_key 等 sensitive=true 的字段
8. 渲染 token_request 的 url/headers/query/body
9. 按 body_type=json/form/raw 请求认证服务
10. 缓存完整响应体，不落盘
11. 根据 inject 规则中的 `{ $.path }` 从响应体取字段并注入 headers/query/body
12. 模型认证失败时清理缓存并重试
14. 生成 ResolvedModelAuth
15. 构造模型客户端并调用模型
```

运行时结果：

```python
ResolvedModelAuth(
    api_key=None,
    default_headers={
        "Authorization": "Bearer dynamic-access-token"
    },
    default_query={},
    default_body={},
)
```

动态 token 的关键点：

```text
临时 token 不写入数据库
临时 token 不写入 Credential Vault
临时 token 不返回前端
临时 token 不打印日志
只存在后端内存缓存中
```

## 7. 数据结构设计

### 7.1 ModelConfig

现有模型配置增加 `auth_credential_id`，不再新增或继续扩展模型内的 `auth_config`：

```python
class ModelConfig(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str | None
    model_name: str
    context_window: int | None
    is_system: bool
    user_id: str | None
    is_active: bool
    auth_credential_id: str | None = None
```

兼容策略：

```text
老数据没有 auth_credential_id：按当前逻辑调用
auth_credential_id = None：无认证
auth_credential_id 指向 kind = model_auth：解析对应模型认证凭据
老数据仍存在 auth_config：进入迁移/兼容逻辑，见 7.6
老数据仍存在 api_key：可兼容读取，并迁移为 static_headers 默认 Authorization 模板
```

### 7.2 凭据基础结构

凭据管理扩展为两类：

```text
kind = basic
kind = model_auth
```

`kind = basic` 用于保留通用账号密码、普通系统凭据、MCP 等已有场景。

`kind = model_auth` 用于模型认证，包含静态 Header、API Key、动态 token 以及未来模型认证类型。

```python
class Credential(BaseModel):
    id: str
    user_id: str
    name: str
    description: str | None = None
    kind: Literal["basic", "model_auth"] = "basic"
    basic: BasicCredential | None = None
    model_auth: ModelAuthCredential | None = None
```

### 7.3 静态 Header 配置

```python
class ModelAuthVariable(BaseModel):
    value: Any
    sensitive: bool = True
```

```python
class StaticHeadersConfig(BaseModel):
    headers: dict[str, str] = {}
    query: dict[str, str] = {}
    body: dict[str, Any] = {}
```

```python
class ModelAuthCredential(BaseModel):
    version: int = 2
    type: Literal["static_headers", "dynamic_token"]
    static_headers: StaticHeadersConfig | None = None
    token_request: TokenRequestConfig | None = None
    inject: TokenInjectConfig | None = None
    variables: dict[str, ModelAuthVariable] = {}
```

示例：

```json
{
  "version": 2,
  "type": "static_headers",
  "static_headers": {
    "headers": {
      "Authorization": "Bearer {{ variables.api_key }}"
    },
    "query": {},
    "body": {}
  },
  "variables": {
    "api_key": {
      "value": "sk-xxx",
      "sensitive": true
    }
  }
}
```

### 7.4 动态 Token 配置

```python
class TokenRequestConfig(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH"]
    url: str
    headers: dict[str, str] = {}
    query: dict[str, str] = {}
    body_type: Literal["json", "form", "raw"] = "json"
    body: Any = {}
```

```python
class TokenInjectConfig(BaseModel):
    headers: dict[str, str] = {}
    query: dict[str, str] = {}
    body: dict[str, Any] = {}
```

```python
class DynamicTokenModelAuthCredential(BaseModel):
    version: int = 2
    type: Literal["dynamic_token"]
    token_request: TokenRequestConfig
    inject: TokenInjectConfig
    variables: dict[str, ModelAuthVariable] = {}
```

动态 token 的所有持久化配置都在 `credential.model_auth` 中：

```text
token_request
inject
variables
```

模型配置不保存这些字段。

### 7.5 运行时解析结果

```python
class ResolvedModelAuth(BaseModel):
    api_key: str | None = None
    default_headers: dict[str, str] = {}
    default_query: dict[str, str] = {}
    default_body: dict[str, Any] = {}
```

`ResolvedModelAuth` 是运行时对象，不持久化。

要求：

```text
不落库
不返回前端
不写入 session
不打印到日志
只用于创建模型客户端
```

### 7.6 老 auth_config 迁移和兼容

旧方案中 `ModelConfig.auth_config` 可能保存：

```text
type = static_headers
type = dynamic_token
credentials alias 列表
headers/query 模板
token_request
inject
```

新方案中这些内容迁移到 `credentials.kind = model_auth`：

```text
ModelConfig.auth_config.type -> credential.model_auth.type
ModelConfig.auth_config.headers/query -> credential.model_auth.static_headers
ModelConfig.auth_config.token_request -> credential.model_auth.token_request
ModelConfig.auth_config.inject -> credential.model_auth.inject
ModelConfig.auth_config.credentials + 旧 Credential 字段 -> credential.model_auth.variables
ModelConfig.auth_config 不再作为主数据源
ModelConfig.auth_credential_id -> 新建或复用的 model_auth credential id
```

兼容读取策略：

```text
优先读取 auth_credential_id
如果 auth_credential_id 为空但 auth_config 存在，使用旧 auth_config 解析一次
解析成功后可在保存模型或后台迁移时生成 model_auth 凭据
迁移完成后写入 auth_credential_id
旧 auth_config 可保留只读一段时间，避免回滚风险
```

API Key 迁移策略：

```text
老模型只有 api_key：生成 kind = model_auth 的 static_headers 凭据
默认 headers.Authorization = "Bearer {{ variables.api_key }}"
variables.api_key.sensitive = true
ModelConfig.auth_credential_id 指向该凭据
老 api_key 字段在兼容期可保留只读，不作为新写入目标
```

旧 `credentials` 引用迁移到统一变量池时：

```text
alias.username -> variables.{alias}_username
alias.password -> variables.{alias}_password
alias.domain -> variables.{alias}_domain
password/client_secret/token 等敏感字段 sensitive=true
tenant/domain/client_id 等非敏感字段可 sensitive=false
模板同步改写为 {{ variables.xxx }}
```

## 8. 与现有凭据管理的关系

现有凭据管理继续复用，但需要从单一账号密码结构扩展为 `kind` 分流。

### 8.1 basic 凭据

`kind = basic` 保留现有语义：

```python
class BasicCredential(BaseModel):
    username: str = ""
    password: str = ""
    domain: str = ""
```

适用场景：

```text
账号密码
通用 token
已有 MCP credential binding
非模型认证的系统凭据
```

### 8.2 model_auth 凭据

`kind = model_auth` 保存模型认证规则：

```python
class ModelAuthCredential(BaseModel):
    version: int = 2
    type: Literal["static_headers", "dynamic_token"]
    static_headers: StaticHeadersConfig | None = None
    token_request: TokenRequestConfig | None = None
    inject: TokenInjectConfig | None = None
    variables: dict[str, ModelAuthVariable] = {}
```

`variables` 是统一字段池：

```text
所有模板引用都从 variables 取值
字段级 sensitive 决定是否加密
sensitive=true 的值不回显
sensitive=false 的值可以作为普通配置回显
```

### 8.3 用户可在凭据页面编辑

模型页创建的认证凭据会出现在凭据管理页面。

用户可以编辑：

```text
name
description
认证类型允许的配置字段
static_headers.headers/query/body
token_request
inject
variables
variables.*.sensitive
```

编辑影响：

```text
所有引用该 auth_credential_id 的模型都会使用更新后的认证配置
编辑 sensitive=true 字段时，不回显旧值；留空表示不变，填写表示替换
删除变量前应检查模板引用，避免保存后运行失败
```

### 8.4 为什么要结合现有凭据管理

原因：

```text
现有 Credential Vault 已经解决加密存储
已有用户级隔离
已有凭据列表、创建、编辑、删除接口
已有运行时解密能力
MCP credential binding 已经验证了 alias + template 的模式
```

因此模型认证应该复用它，而不是另起一套模型 secret 存储。

## 9. 注入到模型请求的方式

认证解析发生在创建模型客户端之前。

建议运行时入口：

```python
async def get_llm_model_for_user(
    config: dict | None,
    user_id: str | None,
    max_tokens_override: int | None = None,
    streaming: bool = True,
):
    resolved_auth = await ModelAuthResolver().resolve(config, user_id)
    return build_llm_model(config, resolved_auth, max_tokens_override, streaming)
```

OpenAI-compatible 客户端：

```python
_SafeChatOpenAI(
    model=model_name,
    base_url=base_url,
    api_key=resolved_auth.api_key,
    default_headers=resolved_auth.default_headers,
    default_query=resolved_auth.default_query,
    extra_body=resolved_auth.default_body,
)
```

API Key 默认已经被解析为 `default_headers.Authorization`。如果底层 SDK 强制要求 `api_key` 参数，可以由构造器在兼容层从 `Authorization: Bearer xxx` 中提取或传入占位值，但主数据仍以 `model_auth.static_headers` 为准。

Gemini 客户端：

```text
优先支持 SDK 原生 api_key
Header / Query 能否注入取决于 SDK 能力
如果 SDK 不支持，则管理面应提示该 provider 对额外 Header 支持有限
```

自定义 OpenAI-compatible 网关是第一优先支持对象，因为内部模型网关通常会兼容 OpenAI chat completions 协议。

## 10. 需要覆盖的调用场景

模型认证能力不能只接入普通对话，否则用户会遇到“这里能用、那里不能用”的问题。

需要统一覆盖：

```text
普通对话 DeepAgent
RPA 录制辅助
RPA 运行时 agent
定时任务 schedule 解析
session 标题生成
模型连接测试
后续所有通过模型配置调用 LLM 的功能
```

尤其“模型连接测试”必须走同一套认证解析逻辑。

否则会出现：

```text
测试连接成功，但真实对话失败
测试连接失败，但真实调用成功
```

这类体验会让用户无法判断配置是否正确。

## 11. 示例场景

### 11.1 场景 A：静态公司 Token

用户拿到内部模型网关文档：

```http
POST https://model-gateway.company.com/v1/chat/completions
Authorization: Bearer sk-company
X-Tenant: team-a
```

用户在模型配置页填写：

```text
Base URL: https://model-gateway.company.com/v1
Model Name: claude-opus-4-7
认证方式: 静态 Header
Authorization: Bearer sk-company
X-Tenant: team-a
```

保存后：

```text
创建或更新一条 kind = model_auth 的凭据
Header 模板进入 model_auth.static_headers.headers
Header 值进入 model_auth.variables
ModelConfig 只保存 auth_credential_id
```

调用时：

```http
Authorization: Bearer sk-company
X-Tenant: team-a
```

### 11.2 场景 B：动态 Token

用户所在团队的模型网关要求：

```text
先 POST https://auth.company.com/token
用 client_id/client_secret 换 access_token
再请求模型时带 Authorization: Bearer access_token
```

用户在模型配置页填写：

```text
认证方式: 动态 Token
Token URL: https://auth.company.com/token
Client ID: corp-client
Client Secret: 已配置
测试响应字段: $.access_token
注入方式: Authorization: Bearer {$.access_token}
```

保存后：

```text
token_request/inject/variables 持久化在 credentials.kind=model_auth 的 model_auth 字段
ModelConfig.auth_credential_id 指向该凭据
```

第一次调用：

```text
解析 model_auth 凭据
渲染 token_request
请求认证中心
获取 access_token
缓存 token
调用模型
```

第二次调用：

```text
token 未过期
直接复用缓存
调用模型
```

token 过期后：

```text
重新请求认证中心
刷新缓存
调用模型
```

## 12. 错误处理设计

错误信息要面向用户可理解，而不是直接暴露底层异常。

| 错误场景 | 用户提示 |
| --- | --- |
| Header JSON 不是对象 | 请输入合法的 Header JSON 对象 |
| Header 名称为空 | Header 名称不能为空 |
| 引用的认证凭据不存在 | 模型认证引用的凭据不存在，请重新配置认证信息 |
| 认证凭据类型不正确 | 请选择模型认证类型的凭据 |
| 变量字段不存在 | 模型认证模板引用了不存在的变量，请检查变量配置 |
| 凭据解密失败 | 凭据解密失败，请检查凭据密钥或重新保存凭据 |
| Token URL 请求失败 | 获取动态 Token 失败，请检查 Token URL、网络和凭据 |
| 注入字段取不到 | 注入配置引用的响应字段为空，请检查 `{ $.path }` |
| 模型返回 401/403 | 模型认证失败，请检查 API Key、Header 或动态 Token 配置 |

日志安全要求：

```text
不能打印 Authorization
不能打印 access_token
不能打印 refresh_token
不能打印 client_secret
不能打印完整 token 响应
不能把 ResolvedModelAuth 原样输出到日志
不能打印 sensitive=true 的 variables 值
```

## 13. 分阶段落地建议

### 第一阶段：凭据结构和静态 Header

目标是先解决最紧迫的企业内部固定 token 和 API Key 场景。

包括：

```text
凭据管理扩展 kind = basic / model_auth
ModelConfig 增加 auth_credential_id
模型配置页增加认证配置区域
API Key 保存为 static_headers 默认 Authorization 模板
支持静态 Header 表格模式
支持静态 Header JSON 导入
Header 值保存到 model_auth.variables
variables.*.sensitive 决定是否加密
ModelAuthResolver 支持 static_headers
模型连接测试携带静态 Header
普通对话和 RPA 调用携带静态 Header
老 api_key 和老 auth_config 兼容读取
```

第一阶段应确保旧模型无 `auth_credential_id` 时仍然可用。

### 第二阶段：动态 Token JSON 基础能力

目标是支持企业内部不同形态的动态 token 认证。

包括：

```text
credential.model_auth.type = dynamic_token
TokenRequestConfig
TokenInjectConfig
ModelAuthResolver dynamic_token 分支
完整响应体字段注入
内存响应 cache
并发刷新锁
放开管理面动态 Token 入口
管理面动态 Token JSON 配置
统一变量字段池
headers/query/body 模板渲染
json/form/raw token 请求体
headers/query/body token 注入
```

### 第三阶段：凭据管理产品化和迁移收口

目标是把模型认证凭据作为可管理资产沉淀下来。

包括：

```text
OAuth2 Client Credentials 简单模式 UI
模型页临时新建认证的 name/description 自动生成
凭据页面支持编辑 model_auth 的 name/description 和配置
老 auth_config 后台迁移任务
老 api_key 迁移为 static_headers 默认 Authorization 模板
迁移状态观测和失败重试
更多认证类型扩展
调试工具和更详细的连接测试结果
```

## 14. 遗留事项

### 静态 Token 是否支持 Body 配置

新结构中 `static_headers` 已预留 `body` 字段，但第一阶段仍建议只开放 Header 和 Query，Body 先作为后端结构预留。

原因是现阶段最明确、最紧急的业务诉求是“模型网关要求额外 Header”，而静态 Body 会影响底层模型请求体结构。不同模型 SDK 对请求体扩展字段的支持差异较大，如果提前开放，容易让用户配置出“界面可保存但运行时不一定生效”的能力。

后续需要结合真实网关场景再判断是否开放：

```text
是否存在必须通过请求 body 传固定认证字段的模型网关
底层 OpenAI-compatible / Gemini / 其他 SDK 是否都能稳定透传额外 body
Body 字段的类型、嵌套结构和 secret 保护规则是否需要与动态 Token 共用
连接测试、普通对话、RPA 录制和运行是否都能一致携带 body 扩展
```

在这个问题明确前，静态 Token 管理面只开放 Header 和 Query；动态 Token 仍然保留 headers/query/body 的扩展设计，因为它本质上描述的是“先请求认证服务，再把 token 注入模型请求”的完整运行面规则。

## 15. 验收标准

### 管理面

```text
用户可以在模型配置页选择无认证
用户可以继续使用 API Key 输入体验
API Key 保存为 static_headers 默认 Authorization 模板
用户可以通过表格添加多个静态 Header
用户可以通过 JSON 导入静态 Header
非法 JSON 会被阻止并提示
编辑模型时不回显 sensitive=true 的 secret，只显示已配置
用户可以删除某个 Header
用户可以替换某个 Header 值
动态 Token 入口可见，并在支持后可配置 token_request/inject/variables
模型页临时新建认证时自动生成 name/description
凭据页面可以编辑模型认证的 name/description 和配置
```

### 存储安全

```text
ModelConfig 中不保存明文 Header 值
ModelConfig 中不保存 token_request/inject/variables
ModelConfig 只保存 auth_credential_id
credentials.kind = model_auth 中保存模型认证配置
variables.*.sensitive 决定字段值是否加密
接口返回模型列表时不返回明文认证值
日志不打印 secret
```

### 运行面

```text
静态 Header 能注入模型连接测试
API Key 能以默认 Authorization Header 注入模型连接测试
静态 Header 能注入普通对话
静态 Header 能注入 RPA 录制辅助
动态 Token 首次调用会请求 token endpoint
动态 Token 未过期时复用缓存
动态 Token 过期后自动刷新
动态 Token 获取失败时不继续调用模型
动态 Token 请求支持 headers/query/body 中任意位置携带 variables
动态 Token 响应支持从嵌套对象或数组路径提取 token
老 auth_config 在兼容期仍可解析
```

## 16. 核心结论

这套设计的关键不是把某个 token 写到请求头里，而是建立一条稳定的模型认证链路：

```text
用户在管理面配置认证方式
模型只保存 auth_credential_id
敏感值统一进入 credentials.kind=model_auth 的 variables
字段级 sensitive 决定是否加密
动态 token 的 token_request/inject/variables 持久化在 credential.model_auth
运行时 ModelAuthResolver 解析出最终认证参数
底层模型客户端只消费 ResolvedModelAuth
```

这样做之后：

```text
API Key 不再是单独类型，而是 static_headers 的默认 Authorization 模板
静态 token 场景可以快速满足
动态 token 场景有清晰扩展点
现有凭据管理得到复用和扩展
模型调用链路保持统一
未来新增认证协议不需要重写业务调用逻辑
```

## 17. 本次更新章节

本次围绕最新模型认证方案更新了以下章节：

```text
2. 整体设计原则：改为模型只保存 auth_credential_id，认证配置进入 model_auth 凭据
3. 管理面用户如何使用：补充 API Key 默认 Authorization 模板、临时新建认证命名规则、凭据页可编辑
4. 运行面整体链路：改为 auth_credential_id -> Credential kind=model_auth -> ModelAuthResolver
5. 静态 Token 运行流程：替换旧 auth_config 示例，改为 static_headers + variables
6. 动态 Token 运行流程：说明 token_request/inject/variables 均持久化在 credential.model_auth
7. 数据结构设计：新增 kind=basic/model_auth、auth_credential_id、variables 字段池和老 auth_config 迁移兼容
8. 与现有凭据管理的关系：扩展 basic/model_auth 两类凭据和凭据页编辑能力
9. 注入到模型请求的方式：调整 API Key 作为 static_headers 的运行时说明
11. 示例场景：更新静态 Header 和动态 Token 的保存位置
12. 错误处理设计：补充 model_auth 凭据、变量字段和 sensitive 日志要求
13. 分阶段落地建议：调整为凭据结构、动态 Token、迁移收口三阶段
14. 遗留事项：保留静态 Body 预留但管理面暂缓开放
15. 验收标准：补充 auth_credential_id、model_auth、variables、迁移兼容验收项
16. 核心结论：总结最新方案关键点
```
