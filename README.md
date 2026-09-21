<p align="center">
  <img src="frontend/public/favicon.svg" width="80" height="80" alt="Account Manager 图标">
</p>

<h1 align="center">Account Manager</h1>

<p align="center">团队共享账号 · 访问授权 · 登录验证</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="docs/usage.md">使用指南</a> ·
  <a href="docs/deployment.md">部署文档</a> ·
  <a href="docs/development.md">开发指南</a>
</p>

面向团队的共享账号管理系统，集中维护供应商账号、访问权限、领用名额和使用状态。通过中文 Web 界面完成账号分配、领用、额度反馈与归还，支持桌面和移动端。

采用 React + TypeScript、FastAPI 和 PostgreSQL，提供 RESTful API，使用 Docker Compose 部署。

## 功能

- **账号管理**：批量导入账号邮箱、账号密码和邮箱密码，支持在线配置档位、到期时间、停用与恢复。
- **权限分配**：管理员与普通用户角色；按用户组或指定用户授权，支持多组归属。
- **领用与归还**：分别限制账号同时使用人数和用户领用数量，归还时登记额度或异常。
- **状态跟踪**：记录剩余额度、重置时间与异常；在线维护异常类别及各类别的恢复间隔，支持可配置的耗尽阈值和到期事件自动结算。
- **登录验证**：按需从已适配邮箱提取目标服务的验证码并显示邮件接收时间；管理员维护账号 2FA 配置，领用者获取并刷新验证码。
- **取码工具**：通过配置组合邮箱后端、邮件模板与匹配参数；管理员按账号选择已注册工具，也可关闭自动取码。
- **操作历史**：保留领用、反馈、授权变更与管理员操作记录，方便追溯。
- **响应式界面**：桌面表格、移动卡片，支持搜索、多选状态筛选、排序和分页。

## 快速开始

需要 Docker Engine、Docker Compose v2 和 Python 3。前后端依赖在容器中构建，无需在宿主机安装 Node.js。

在项目根目录执行：

```sh
cp .env.example .env
./ops/start.sh
```

默认访问 `http://localhost:8080`。可在 `.env` 中调整端口、监听地址、镜像缓存和 HTTPS 来源；配置方法见[部署指南](docs/deployment.md)。

初始用户名为 `admin`，随机初始密码保存在本机：

```sh
cat .local/secrets/bootstrap_password
```

首次登录需修改密码。后续启动保留已有用户、密码和数据。

站点登录密码要求 15–72 个字符，并校验常见密码、重复或连续字符及易猜的用户信息组合；支持长口令、空格和中文。完整策略见[使用指南](docs/usage.md#登录密码)。

```sh
# 查看服务状态。
./ops/compose.sh ps

# 查看应用日志。
./ops/compose.sh logs --tail=100 api worker
```

## 使用流程

1. 管理员创建用户和用户组，批量登记供应商账号并配置可见范围。
2. 用户在账号大厅领用账号，在“我的领用”查看密码并反馈使用情况。
3. 正常归还时登记额度与重置时间，异常归还时填写类别或说明，释放个人领用名额。

批量导入格式为每行一个账号，同批选择统一档位：

```text
account@example.test----example-password----example-mailbox-password
```

具体权限、名额计算、状态规则和管理员操作见[使用指南](docs/usage.md)。账号可用状态来自用户反馈和登记时间的推算，系统不会自动登录供应商服务检测账号。

邮箱取码与二维码配置见[登录验证指南](docs/verification.md)。系统仅在用户主动取码时访问邮箱。

## 项目结构

```text
backend/       API、业务逻辑、数据库迁移与后端测试
frontend/      Web 界面与浏览器测试
deploy/        Dockerfile、docker-compose.yaml 与 Nginx 配置
ops/           启动、构建、测试、备份与恢复工具
docs/          使用、部署和开发文档
.env.example   环境配置示例
```

部署配置集中在 `deploy/`，使用 `ops/compose.sh` 统一执行 Compose 命令。私有配置放在根目录 `.env`，密钥和备份放在 `.local/`，均由 Git 忽略。

## 数据与安全

Web、API、后台任务和数据库分别运行。账号、权限、会话和操作历史保存在 PostgreSQL 持久卷中，应用容器重建不会清空数据。

用户密码使用 Argon2id 哈希，供应商密码、2FA 密钥及短期邮箱验证码加密保存；二维码原图与邮件正文不落盘。数据库使用独立 Docker 持久卷，完整恢复需要同时保留数据库快照与凭据加密密钥。备份恢复和 HTTPS 接入说明见[部署指南](docs/deployment.md)。

登录密码计算设有跨实例共享的总量与并发限制；失败计数器有容量上限和自动过期清理，避免随机用户名持续消耗计算与存储资源。参数及重试行为见[登录资源保护](docs/deployment.md#登录资源保护)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [使用指南](docs/usage.md) | 账号导入、授权、领用归还、额度与异常规则 |
| [登录验证](docs/verification.md) | 邮箱验证码、邮件接收时间、账号 2FA 配置与刷新 |
| [取码工具与插件](docs/mail-plugins.md) | 邮箱后端与模板接口、工具组合配置及测试约定 |
| [部署与运维](docs/deployment.md) | 配置、镜像缓存、HTTPS、更新与备份恢复 |
| [架构与开发](docs/development.md) | 服务架构、REST API、本地开发、后端与浏览器测试 |

REST API 位于 `/api/v1`，OpenAPI 定义位于 `/api/openapi.json`。测试使用独立数据库或测试部署，运行方法见[开发指南](docs/development.md)。
