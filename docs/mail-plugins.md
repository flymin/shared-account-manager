# 邮箱后端与邮件模板插件

邮箱后端负责访问邮箱，邮件模板负责识别验证邮件。两类插件独立组合，账号只需选择邮箱后端；所有已配置的模板参与邮件识别，无需逐账号选择模板。

## 结构与内置插件

| 文件 | 职责 |
| --- | --- |
| `backend/app/mail.py` | `Mailbox`、`EmailTemplate` 协议与标准数据类型，通用候选查找流程 |
| `backend/app/plugins/__init__.py` | 显式注册表、默认后端和插件加载入口 |
| `backend/app/plugins/mailcom.py` | mail.com 邮箱后端，插件 ID 为 `mailcom` |
| `backend/app/plugins/template_a.py` | 匿名的 Template A，插件 ID 为 `template_a` |
| `backend/app/plugins/html.py` | 内置插件共用的 HTML 解析辅助代码 |
| `backend/app/email_worker.py` | 任务租约、持久化候选、取消检查和读取结果发布 |

核心层只通过接口和注册表使用插件，不导入具体实现。数据库会保存后端 ID，因此发布后应保持 ID 稳定。插件是随应用部署的受信任 Python 代码，不支持用户上传代码、提交模块路径或填写任意服务地址。

## 邮箱后端接口

工厂函数接收账号邮箱和邮箱密码，返回实现 `Mailbox` 协议的客户端。构造函数应只初始化本地状态，网络访问从轮询开始。

| 方法 | 约定 |
| --- | --- |
| `iter_unread(since, deadline)` | 按接收时间从新到旧返回 `Message`；每轮设置有限的网络预算，并遵守任务截止时间 |
| `read_raw(message_id)` | 返回 RFC 5322 邮件原文 `bytes`；沿用本轮网络预算，不改变已读状态 |
| `mark_read(message_id, *, before_write, deadline)` | 写入前调用权限检查回调；检查失败不得写入；确认邮件确已读后才返回 |
| `close()` | 释放连接、Cookie、令牌与内存中的密码 |

`Message` 包含 `message_id`、`sender`、`subject`、`received_at`。接收时间必须是邮箱确认的带时区时间，不能拿发信时间或读取时间替代；无法确定时抛出 `MailError("received_time")`。消息 ID 在同一后端和账号内应稳定、唯一。

核心层会再次验证接收时间窗口和去重记录，使用模板预筛选后才下载原文。每轮最多检查 2000 封邮件、单封原文最多 2 MiB；后端还须在网络流读取期间限制响应大小和耗时，不能依赖下载完成后的检查。内置后端的读取轮次最多 60 秒，标记已读轮次最多 30 秒，两者均受任务截止时间限制。

插件使用 `MailError` 返回稳定的错误键，暂时性网络错误可指定 `retryable=True`。不要在异常或日志中包含完整 URL、邮件正文、Cookie、令牌或密码。插件应自行限制请求目标及重定向范围，不能将邮箱返回的任意地址直接当作可信目标。

内置后端将读取与修改已读状态分开处理，并使用邮箱提供的接收时间。密码登录遇到额外验证时返回错误；核心不会将保存的邮箱 2FA 配置传给插件。

## 邮件模板接口

模板工厂不访问网络、不读写数据库；配置有效时返回实现 `EmailTemplate` 的对象，否则返回 `None`。同一轮使用同一个模板对象，确保列表筛选与原文校验使用相同配置。

| 方法 | 约定 |
| --- | --- |
| `matches(sender, subject)` | 以列表元数据判断邮件是否值得读取；返回布尔值 |
| `extract_code(raw)` | 再次校验原文的发件人和主题，检查正文；唯一匹配时返回代码，否则返回 `None` |

模板负责代码的格式和长度规则。核心仅要求结果为 1–128 个可显示字符、无首尾空白，不假设代码一定是六位数字。多个模板从同一封邮件提取出不同代码时，该邮件不会采用或标记已读。

Template A 保留六位数字规则，解析纯文本与 HTML，忽略附件、嵌套邮件、脚本、样式及链接中的数字；正文中出现多个不同代码则放弃。配置沿用以下两个环境变量，实际值只保存在部署环境中：

```dotenv
MAIL_CODE_SENDER=noreply@login.example.test
MAIL_CODE_SUBJECT_KEYWORD=ExampleService
```

上述均为虚构示例。发件人地址精确匹配，主题关键词不区分大小写。配置缺失或无效时 Template A 不启用；没有已配置模板时，API 拒绝新任务，worker 也不访问邮箱。

## 添加插件

1. 在 `backend/app/plugins/` 新建实现文件，遵守对应协议。可参考内置后端或 Template A。
2. 在同目录的 `__init__.py` 导入实现并注册。新后端加入 `MAIL_BACKENDS`，新模板加入 `EMAIL_TEMPLATES`；注册键必须与描述对象中的 ID 一致。
3. 如果新增部署变量，同步 `.env.example` 和 Compose 的应用环境配置，使 API 与 worker 收到相同值。
4. 添加模拟测试，重新构建并部署 API、worker。管理员选择框从 API 读取后端选项，无需增加前端硬编码。

注册示例（`ExampleMailbox` 和 `TemplateB` 为待实现的虚构插件）：

```python
MAIL_BACKENDS["example"] = MailBackendPlugin(
    "example", "Example Mail", ExampleMailbox
)
EMAIL_TEMPLATES["template_b"] = EmailTemplatePlugin(
    "template_b", "Template B", TemplateB.from_environment
)
```

无需为新后端或模板修改 worker、任务状态机或数据库约束。修改全局默认后端不会自动改写已有账号。移除仍被账号引用的后端后，这些账号会提示插件不可用，不会回退到另一个服务；管理员可重新选择或关闭自动取码。

## 账号选择与任务一致性

管理员通过 `GET /api/v1/mail-backends` 获取 `default` 与 `items`。批量导入同批共用一个 `mail_backend`，编辑可逐账号修改；UI 清空选择会提交 `null`。API 对导入与预览中省略的字段使用默认后端，对编辑中省略的字段保持原值，拒绝未知 ID 和空字符串。

任务保存创建时的后端，候选消息按账号和后端共同去重。管理员修改后端或邮箱密码时，在同一事务内取消当前任务、清除结果并使租约失效，同时递增账号的邮箱配置版本。页面通过状态响应中的 `email_config_version` 清除旧结果，即使新后端仍可取码也不会保留旧代码。核心在保存候选、标记已读和发布结果前再次检查任务与权限；后端必须在实际发出写请求前调用 `before_write`，包括登录或令牌刷新之后。已经发出的网络请求无法撤回，但其迟到结果不会再次发布。

先持久化加密候选、再标记已读，保证重启后可重试同一候选；成功标记后才向发起者显示代码及接收时间。邮件正文和二维码原图不落盘，插件会话仅保存在进程内。

## 测试

后端测试使用内存响应和虚构账号，不连接真实邮箱。至少覆盖：协议与超时、读取不改变已读状态、写前取消、重启恢复、后端变更、空选项与权限、列表和原文校验、模板歧义，以及多个后端中相同消息 ID 的隔离。

现有用例包括 `test_mailcom.py`、`test_email_templates.py`、`test_mail_backends.py`、`test_email_worker.py` 和迁移测试。新增模板测试应覆盖与内置模板不同的代码格式，确保核心没有格式耦合；浏览器用例覆盖导入预览、编辑、普通用户显示和桌面/移动端布局。运行方法见[开发指南](development.md)。
