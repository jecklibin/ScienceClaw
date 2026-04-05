# RPA 密码脱敏与凭据保险箱设计

## 背景

RPA 录制技能时，用户在密码字段中输入的内容会被明文显示在左侧步骤列表中，参数配置页面也直接暴露密码。生成的技能脚本中密码被硬编码，无法安全托管。

本设计解决三个问题：
1. 录制时密码字段识别与 UI 脱敏
2. 凭据的安全存储（AES-256-GCM 加密）
3. 技能执行时凭据的安全注入

## 核心决策

- 敏感数据识别范围：仅 `input[type="password"]` 字段
- 加密方案：应用层 AES-256-GCM 对称加密
- 存储：Local 模式存本地加密文件，Docker 模式存 MongoDB（值加密）
- 凭据与技能解耦：独立 Credential Vault，一个凭据可被多个技能复用
- 密码不传回后端：浏览器端检测到密码字段后，value 替换为占位符 `{{credential}}`
- 执行时注入：后端层（local_preview_backend / full_sandbox_backend）解密凭据并注入 kwargs

---

## 一、敏感字段识别与录制脱敏

### 浏览器端（CAPTURE_JS in manager.py）

`fill` 事件处理器增加密码检测：

```javascript
if (el.type === 'password') {
  event.sensitive = true;
  event.value = '{{credential}}';  // 真实密码不传回后端
}
```

### 后端数据模型（RPAStep）

`RPAStep` 新增字段：

```python
sensitive: bool = False
```

后端收到 `sensitive: true` 的步骤时，确保 value 为 `{{credential}}`（防御性校验）。

### 前端步骤列表（RecorderPage.vue）

`sensitive` 步骤的 value 显示为 `*****`，description 中的密码值也替换为 `*****`。

### 前端配置页面（ConfigurePage.vue）

sensitive 参数：
- 不显示明文值
- 参数值改为下拉选择已有凭据（从凭据 API 获取列表）
- 选中后存储 `credential_id`

---

## 二、Credential Vault 后端

### 模块结构

```
ScienceClaw/backend/credential/
├── __init__.py
├── models.py      # Pydantic 模型
└── vault.py       # 加密/解密 + 存储
```

### 数据模型（models.py）

```python
class Credential(BaseModel):
    id: str                    # "cred_" + nanoid
    name: str                  # 显示名称，如 "GitHub 登录"
    username: str              # 用户名
    encrypted_password: str    # AES-GCM 加密后的 base64 字符串
    domain: str = ""           # 可选，适用网站域名
    created_at: datetime
    updated_at: datetime
```

### 加密（vault.py）

```python
class CredentialVault:
    def __init__(self, key: bytes):
        """key: 32 bytes from CREDENTIAL_KEY env var."""

    def encrypt(self, plaintext: str) -> str:
        """AES-256-GCM encrypt, return base64(nonce + ciphertext + tag)."""

    def decrypt(self, encrypted: str) -> str:
        """Decode base64, extract nonce/tag, decrypt."""
```

- 加密密钥从 `CREDENTIAL_KEY` 环境变量读取（32 字节 hex 或 base64）
- 首次启动若不存在，自动生成随机密钥并写入 `.env`
- 每次加密使用随机 nonce（12 bytes），与密文一起存储

### 存储后端

- Local 模式：`~/.scienceclaw/credentials.enc`（JSON 文件，password 字段加密）
- Docker 模式：MongoDB `credentials` 集合（password 字段加密）
- 通过 `STORAGE_BACKEND` 环境变量自动选择

### REST API（route/credential.py）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/v1/credentials` | 列表（不返回密码） |
| POST | `/api/v1/credentials` | 创建凭据 |
| PUT | `/api/v1/credentials/{id}` | 更新凭据 |
| DELETE | `/api/v1/credentials/{id}` | 删除凭据 |

API 响应中永远不返回密码明文或密文。创建/更新时接收明文密码，加密后存储。

---

## 三、执行时凭据注入

### 参数配置数据结构

技能参数中 sensitive 参数的格式：

```json
{
  "password": {
    "original_value": "{{credential}}",
    "sensitive": true,
    "credential_id": "cred_abc123"
  }
}
```

### generator.py 改动

`_maybe_parameterize()` 对 sensitive 参数：
- 生成 `kwargs['password']`（无默认值，不嵌入明文）
- 不使用 `kwargs.get('password', '明文')` 形式

### 注入点

两个执行后端在调用 `execute_skill(page, **kwargs)` 前注入：

1. `local_preview_backend.py`：
   - 检测 kwargs 中的 credential 引用参数
   - 调用 `CredentialVault.decrypt()` 获取明文
   - 合并到 kwargs

2. `full_sandbox_backend.py`：
   - 同样的逻辑：解密凭据，注入 kwargs
   - 明文密码只在后端进程内存中短暂存在

脚本本身不感知凭据系统的存在，只接收普通的 kwargs 参数。

---

## 四、前端凭据管理页面

### 路由

`main.ts` 中添加 `/credentials` 路由。

### 页面功能

- 列表展示所有凭据：名称、用户名、域名、创建时间
- 密码列始终显示 `*****`
- 新增凭据：表单包含名称、用户名、密码（password input）、域名（可选）
- 编辑凭据：密码字段为空表示不修改
- 删除凭据：确认对话框

### 组件

复用现有 Reka UI 组件（Dialog、Input、Button、Table）。

---

## 五、数据流总览

```
录制阶段:
  浏览器 fill password → CAPTURE_JS 检测 type="password"
    → event.sensitive=true, event.value="{{credential}}"
    → 后端存储 RPAStep(sensitive=true, value="{{credential}}")
    → 前端步骤列表显示 "*****"

配置阶段:
  ConfigurePage 检测 sensitive 参数
    → 下拉选择已有凭据 → 存储 credential_id

执行阶段:
  local_preview_backend / full_sandbox_backend
    → 读取参数配置中的 credential_id
    → CredentialVault.decrypt() 获取明文
    → 注入 kwargs → execute_skill(page, password="明文")
    → 执行完毕，明文从内存释放
```

## 六、改动文件清单

| 文件 | 改动 |
|------|------|
| `backend/rpa/manager.py` | CAPTURE_JS 中 fill 事件检测 password 字段 |
| `backend/rpa/models.py` 或 manager.py 中 RPAStep | 新增 `sensitive: bool` 字段 |
| `backend/credential/__init__.py` | 新模块 |
| `backend/credential/models.py` | Credential Pydantic 模型 |
| `backend/credential/vault.py` | AES-GCM 加密/解密 + 双模式存储 |
| `backend/route/credential.py` | 凭据 CRUD API |
| `backend/main.py` | 注册 credential router |
| `backend/config.py` | 新增 `CREDENTIAL_KEY` 配置 |
| `backend/rpa/generator.py` | sensitive 参数不嵌入默认值 |
| `backend/deepagent/local_preview_backend.py` | 执行前解密凭据注入 kwargs |
| `backend/deepagent/full_sandbox_backend.py` | 执行前解密凭据注入 kwargs |
| `frontend/src/pages/rpa/RecorderPage.vue` | sensitive 步骤显示 `*****` |
| `frontend/src/pages/rpa/ConfigurePage.vue` | sensitive 参数改为凭据下拉选择 |
| `frontend/src/pages/CredentialsPage.vue` | 新增凭据管理页面 |
| `frontend/src/api/credential.ts` | 凭据 API 客户端 |
| `frontend/src/main.ts` | 添加 `/credentials` 路由 |
| `frontend/src/locales/en.ts` | 凭据相关英文文案 |
| `frontend/src/locales/zh.ts` | 凭据相关中文文案 |
