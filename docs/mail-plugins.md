# 邮箱取码工具与插件

**邮箱取码工具 = 邮箱后端 + 邮件模板 + 模板参数。** 开发者实现可复用插件，再通过 TOML 配置注册工具；管理员在账号导入或编辑时选择工具。一次取码只运行所选工具的模板。

同一个后端可以搭配不同模板，同一个模板也可以用于多个后端。即使后端和模板都相同，也可以通过不同参数注册多个工具，用于识别不同服务的验证邮件。

## 代码结构

```text
backend/app/
├── mail.py                      标准接口与通用候选查找流程
└── plugins/
    ├── __init__.py               公共调用入口
    ├── tools.py                  配置加载、工具组装与配置摘要
    ├── tools.toml                内置工具配置
    ├── backends/
    │   ├── __init__.py           后端实现注册表
    │   └── mailcom.py            邮箱访问实现
    ├── templates/
    │   ├── __init__.py           模板实现注册表
    │   └── six_digit_code.py     六位数字验证码模板
    └── common/
        ├── __init__.py
        └── html.py               共用 HTML 解析工具
```

`backends/` 和 `templates/` 各自注册受信任的 Python 实现，互不导入。`tools.py` 只按配置中的注册 ID 组合它们；配置不能指定 Python 导入路径或上传代码。核心业务通过工具入口及 `Mailbox`、`EmailTemplate` 协议调用实现。

`common/html.py` 将 HTML 解析为可遍历的节点树。后端用它查找登录表单、输入字段和内嵌配置，模板用它提取正文文本并跳过脚本、样式和页头。该工具不包含服务商规则、不访问网络，也不用于安全展示 HTML。

## 注册取码工具

内置配置 `backend/app/plugins/tools.toml` 将 `mailcom` 后端与 `six_digit_code` 模板组合为一个名为 `mail.com` 的工具。其工具 ID 保持为 `mailcom`，兼容已有账号的选择；工具 ID 与后端 ID 属于不同的注册表，不要求相同。

工具配置示例：

```toml
default = "example_login"

[[tools]]
id = "example_login"
name = "Example 登录验证码"
backend = "mailcom"
template = "six_digit_code"
revision = 1

[tools.options]
sender = "noreply@login.example.test"
subject_keyword = "ExampleService"

[[tools]]
id = "other_login"
name = "Other 登录验证码"
backend = "mailcom"
template = "six_digit_code"
revision = 1

[tools.options]
sender = "noreply@other.example.test"
subject_keyword = "OtherService"
```

上述域名、发件人和服务名均为虚构示例。前端只展示这里注册的两个工具，不直接列出底层后端和模板。选择第一个工具时，不会尝试第二个工具的规则。

| 配置 | 含义 |
| --- | --- |
| `default` | 新账号未指定工具时使用的工具 ID；省略则默认不启用 |
| `tools[].id` | 稳定且唯一的工具 ID，最长64字符，以小写字母开头，仅含小写字母、数字、下划线和连字符 |
| `name` | 前端显示名称，最长100字符 |
| `backend` | `MAIL_BACKENDS` 中已注册的后端 ID |
| `template` | `EMAIL_TEMPLATES` 中已注册的模板 ID |
| `revision` | 正整数，默认1；实现行为变化但参数不变时递增，使旧任务失效 |
| `options` | 当前工具传给模板工厂的参数，可为字符串、整数、布尔值或环境变量引用 |

环境变量引用写成 `sender = { env = "MAIL_CODE_SENDER" }`。工具层将其解析为字符串后传给模板，模板自身不读取环境变量。缺失的环境变量解析为空字符串；六位数字验证码模板会拒绝缺失或无效的匹配规则。

配置文件最多128 KiB、最多100个工具。重复 ID、无效字段和不存在的默认工具会导致配置加载失败，错误不输出配置内容。未安装的后端或模板不出现在工具列表中；已有账号引用它们时显示工具不可用，不自动切换到其他工具。模板参数未配置或无效时，可以保留账号选择，但自动取码不可用。

## 私有部署配置

可以直接维护内置工具配置，也可以通过 `MAIL_TOOLS_CONFIG_PATH` 指向私有 TOML 文件；私有文件会替换整个内置工具列表，不会与其合并。Compose 相对路径以 `deploy/` 为基准，例如 `.env` 中设置：

```dotenv
MAIL_TOOLS_CONFIG_PATH=../.local/mail-tools.toml
```

API、worker 和初始化容器只读挂载同一个配置文件。文件需要允许容器应用用户读取。直接运行 Python 服务时，使用 `MAIL_TOOLS_CONFIG_FILE` 指向该文件；未设置时使用内置配置。

配置在进程启动时加载。修改 TOML、匹配参数或插件实现后，应统一更新 API 和 worker，避免不同进程使用不同规则；修改路径或环境变量后使用 `./ops/compose.sh up -d --force-recreate --no-build --wait` 重建容器。新增环境变量引用时，也要将对应变量传入 Compose 的应用环境；写在 `.env` 中并不会自动传入容器。

真实发件人、服务标识和私有配置留在 `.env` 或被忽略的 `.local/` 中。数据库保存工具 ID，不保存工具定义；备份部署时需另外保留 TOML 文件、环境变量和对应版本的插件代码。

## 新增插件

1. 邮箱后端实现放入 `backends/`，并在其 `__init__.py` 中加入 `MAIL_BACKENDS`。工厂接收账号邮箱和邮箱密码，返回 `Mailbox` 客户端。
2. 邮件模板实现放入 `templates/`，并在其 `__init__.py` 中加入 `EMAIL_TEMPLATES`。工厂接收工具的 `options` 字典，配置有效时返回 `EmailTemplate`，否则返回 `None`。
3. 在 TOML 中新增工具，将后端 ID、模板 ID 和参数组合起来。复用现有后端只需新增模板并注册工具，无需改 worker、数据库约束或前端选项。
4. 添加模拟测试，重新构建并部署 API 和 worker。工具与插件 ID 发布后应保持稳定。

模板注册示例（假设已实现 `AlphanumericCodeTemplate`）：

```python
from .alphanumeric_code import AlphanumericCodeTemplate

EMAIL_TEMPLATES["alphanumeric_code"] = EmailTemplatePlugin(
    "alphanumeric_code", "Alphanumeric code", AlphanumericCodeTemplate.from_config
)
```

然后在工具配置中使用 `template = "alphanumeric_code"`，仍可使用同一个邮箱后端。

## 接口约定

`Mailbox`、`EmailTemplate`、`Message` 和 `Candidate` 定义于 `backend/app/mail.py`。

| 邮箱后端方法 | 约定 |
| --- | --- |
| `iter_unread(since, deadline)` | 按接收时间从新到旧返回 `Message`；设置有限网络预算并遵守任务截止时间 |
| `read_raw(message_id)` | 返回 RFC 5322 原文 `bytes`；沿用轮询预算，不改变已读状态 |
| `mark_read(message_id, *, before_write, deadline)` | 每次写入前检查权限回调，包括登录或令牌刷新后；确认已读后才返回 |
| `close()` | 释放连接、Cookie、令牌与内存中的密码 |

`Message` 包含消息 ID、发件人、主题和带时区的实际接收时间，不能用发信时间或读取时间替代。消息 ID 在同一后端和账号内应稳定、唯一。构造客户端只初始化本地状态，轮询开始后才访问网络。

| 邮件模板方法 | 约定 |
| --- | --- |
| `matches(sender, subject)` | 只根据列表元数据判断邮件是否值得读取 |
| `extract_code(raw)` | 再次校验原始邮件头并解析正文；返回唯一验证码，否则返回 `None` |

模板工厂和模板方法不访问网络、数据库，也不包含邮箱后端逻辑。模板自行决定验证码格式；核心仅要求结果为1–128个可显示字符、无首尾空白。

`SixDigitCodeTemplate` 适用于正文包含唯一六位数字验证码的邮件，要求 `sender`、`subject_keyword` 两个字符串参数，分别精确匹配发件人、忽略大小写匹配主题关键词。它解析纯文本和 HTML，忽略附件、嵌套邮件、脚本、样式及 URL 中的数字；多个不同代码不会被采用。

核心每轮最多检查2000封邮件，单封原文最多2 MiB。后端还须限制响应大小、网络耗时、目标地址和重定向范围；内置后端的读取轮次最多60秒、标记已读最多30秒，同时受任务截止时间限制。异常使用 `MailError` 返回稳定错误键，不包含完整 URL、邮件正文、Cookie、令牌或密码。

## 账号选择与任务一致性

管理员通过 `GET /api/v1/mail-tools` 获取 `default` 与 `items`。导入、预览和编辑使用 `mail_tool`，返回 `mail_tool` 与 `mail_tool_name`。省略导入字段使用配置默认值，省略编辑字段保持原值；显式 `null` 关闭自动取码。普通用户不能修改工具。

取码任务保存工具 ID、实际后端 ID 和生效配置的摘要。只有该工具的模板参与筛选与解析。管理员更换工具或邮箱密码会取消任务、清除结果并使旧租约失效；工具的后端、模板、参数或版本变化后，旧任务也不能继续写入或发布结果。前端根据账号配置版本和工具摘要清除已有验证码。

候选消息按账号和实际后端去重。同一邮箱后端的不同工具共享去重记录，切换模板不能再次领取一封已使用或被取消任务占用的邮件；不同后端的相同消息 ID 互不影响。

先持久化加密候选，再标记邮件已读，最后发布验证码和接收时间。重启后可恢复相同配置的任务；配置变化则取消。邮件正文和二维码原图不落盘。

迁移 `0008` 将账号的原后端选择保留为同名工具 ID，保留关闭状态、密码、授权和领用记录。旧任务没有模板配置摘要，因此取消未结束任务和仍有效的取码结果，保留消息 ID 去重记录。降级会关闭账号自动取码，防止旧版本误把工具 ID 当作后端 ID；管理员需重新选择后端。

## 测试

测试使用虚构账号、内存邮件和模拟 HTTP 响应，不连接真实邮箱。覆盖工具配置校验与默认值、独立组合、未选模板隔离、参数变更和配置版本、切换工具及后端、已读写入前取消、消息去重、原有数据迁移与降级。浏览器测试覆盖管理员工具选择、普通用户权限和配置变更后清除旧验证码。运行方法见[开发指南](development.md)。
