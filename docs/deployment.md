# 部署与运维

## 环境要求

需要 Docker Engine、Docker Compose v2 和 Python 3。构建阶段使用容器安装前后端依赖，无需在宿主机安装 Node.js 或 Python 业务依赖。构建需要访问镜像仓库、PyPI 和 npm；受限网络可配置镜像缓存、代理和企业 CA。

以下命令均从项目根目录执行。部署文件集中在 `deploy/`，`.env` 与 `.local/` 位于项目根目录。

## 首次启动

```sh
cp .env.example .env
# 按实际环境修改 .env。
./ops/start.sh
```

启动脚本会生成持久密钥、构建镜像、启动 PostgreSQL、执行数据库迁移并初始化管理员，最后等待服务就绪。默认访问 `http://localhost:8080`。

初始用户名为 `admin`，随机初始密码保存在 `.local/secrets/bootstrap_password`。在服务器上读取该文件后登录，首次登录需要修改密码。后续启动不会重置密码；修改密码后，该文件不再代表管理员的当前密码。

```sh
cat .local/secrets/bootstrap_password
./ops/compose.sh ps
curl --fail http://localhost:8080/api/ready
```

请勿将初始密码输出保存到共享日志或提交到仓库。

## 配置

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `IMAGE_PREFIX` | 空 | Docker Hub 镜像缓存前缀，需带末尾 `/`；留空直接拉取 Docker Hub |
| `BIND_ADDRESS` | `0.0.0.0` | Web 监听地址；同机反向代理通常使用 `127.0.0.1` |
| `HTTP_PORT` | `8080` | Web 在宿主机的 HTTP 端口 |
| `PUBLIC_ORIGIN` | 空 | 对外访问来源，如 `https://accounts.example.test`，不带路径 |
| `MAIL_CODE_SENDER` | 空 | Template A 的完整发件人地址，精确匹配 |
| `MAIL_CODE_SUBJECT_KEYWORD` | 空 | Template A 主题必须包含的固定关键词，不区分大小写 |
| `BUILD_NETWORK` | `default` | Docker 构建网络，必要时可设置为 `host` |
| `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` | 当前环境 | 构建阶段使用的网络代理配置 |

`ops/compose.sh` 显式使用 `deploy/docker-compose.yaml` 和根目录 `.env`；未创建 `.env` 时读取 `.env.example`。它接受常规 Compose 参数，并保留 `COMPOSE_PROJECT_NAME` 或 `-p` 指定的项目名。默认 Compose 项目名为 `account-manager`。

```sh
# 校验配置，不启动服务。
./ops/compose.sh config --quiet

# 不使用包装脚本时的等价入口，需要先创建 .env。
docker compose --env-file .env -f deploy/docker-compose.yaml ps
```

Compose 相对路径以配置文件所在目录为基准；镜像构建上下文指向项目根目录，密钥引用指向根目录 `.local/secrets/`。包装脚本会定位项目目录，从其他工作目录调用时也能找到相同配置。相关规则见 [Docker Compose 文档](https://docs.docker.com/reference/cli/docker/compose/)。

### 镜像缓存与企业网络

Harbor 等缓存仓库可以通过以下示例配置，实际地址只写入本地 `.env`：

```dotenv
IMAGE_PREFIX=registry.example.test/dockerhub-cache/
```

`ops/build.sh` 会读取根目录 `.env` 并传递构建代理参数，也支持没有 buildx 的环境。Compose 直接构建时同样支持上述构建变量。

企业 TLS 代理的 CA 可放在 `deploy/build-ca.pem`。该文件被 Git 忽略，只在前端构建阶段用于 npm 的证书校验，不进入最终 Web 镜像。不要关闭证书校验。

### 邮箱取码与 2FA

邮箱取码由 `worker` 发起，需要部署环境能够解析并通过 HTTPS 访问已适配邮箱服务的登录与邮箱接口；构建代理参数不会自动配置 worker 的运行时网络。API、数据库与邮件任务均使用服务端时间，请保持宿主机时钟同步，否则 TOTP 代码可能失效。

邮箱等待时长在 Web 系统设置中调整，默认5分钟。仅支持邮箱密码登录；邮箱要求额外验证时，需要在邮箱服务中手动处理。账号 2FA 的二维码配置和使用权限见[登录验证指南](verification.md)。

内置 Template A 需要同时配置 `MAIL_CODE_SENDER` 和 `MAIL_CODE_SUBJECT_KEYWORD`。例如下列虚构值，部署时替换为实际验证邮件的发件人和稳定的主题关键词：

```dotenv
MAIL_CODE_SENDER=noreply@login.example.test
MAIL_CODE_SUBJECT_KEYWORD=ExampleService
```

未配置或规则无效时，此模板不启用；没有可用模板时，取码请求会提示管理员配置，后台不会发起邮箱访问。匹配规则仅决定筛选哪些邮件，邮箱服务由账号的“邮箱后端”选择决定；该选项为空时自动取码关闭。实际匹配值只保存在部署环境的 `.env` 中。新增邮箱后端与模板见[插件开发指南](mail-plugins.md)，API 和 worker 必须使用相同的插件代码及配置。

## HTTPS 与反向代理

应用提供 HTTP，推荐使用 Caddy 接入 HTTPS，也可以使用部署环境中已有的反向代理。反向代理和证书的部署方式由实际环境决定，本项目不包含相应配置。

应用侧通过 `.env` 设置监听地址、端口和对外访问来源。同机代理可使用：

```dotenv
BIND_ADDRESS=127.0.0.1
HTTP_PORT=8080
PUBLIC_ORIGIN=https://accounts.example.test
```

代理应保留原始 Host。应用按 `PUBLIC_ORIGIN` 校验写请求；设置为 HTTPS 来源后，登录 Cookie 会启用 Secure。修改配置后执行：

```sh
./ops/compose.sh up -d --no-build --wait
```

## 日常维护与更新

```sh
./ops/compose.sh ps
./ops/compose.sh logs --tail=100 api worker
./ops/compose.sh restart api worker web
```

更新代码后，先备份再构建和启动：

```sh
python3 ops/backup.py
./ops/build.sh
./ops/compose.sh up -d --no-build --wait
```

`init` 服务负责数据库迁移与幂等初始化。就绪接口 `/api/ready` 验证数据库连接及初始化状态；worker 每30秒结算已到期事件、每秒调度持久化邮箱任务，遇到数据库暂时不可用时重试。该就绪检查不代表邮箱服务当前可访问。

从旧版本升级时，先补齐上述邮箱匹配配置。迁移会将已有账号 2FA 类型统一为 `service`，保留密文、配置版本和更新时间，并同步审计中的类型标识；邮箱 2FA 的历史配置继续保留但不启用。升级后刷新浏览器，外部 API 客户端应使用 `/two-factor/service` 和 `two_factor.service`。

邮箱插件迁移会为现有账号和取码任务登记原内置后端，保留账号密码、领用及验证码数据。新账号可通过管理员界面关闭自动取码。降级至不支持邮箱后端选择的版本会丢失此选择，并取消未结束任务、清除短期验证码；应先备份，按需恢复数据。

停止服务可使用 `./ops/compose.sh stop`；删除容器但保留数据库卷可使用 `./ops/compose.sh down`。恢复运行使用 `./ops/compose.sh up -d --no-build --wait`。`down -v` 会删除数据卷，不能用于常规重启或更新。

## 数据存储与备份

| 内容 | 存放位置 |
| --- | --- |
| 账号、用户、授权、会话、历史、设置、加密的 2FA 配置与短期邮箱任务 | PostgreSQL 命名卷，默认 `account-manager_db-data` |
| 数据库密码、凭据加密密钥、初始管理员密码 | `.local/secrets/` |
| 部署参数 | 根目录 `.env` |
| 默认备份目录 | `.local/backups/` |

```sh
python3 ops/backup.py
# 也可以指定一个尚不存在的目标目录。
python3 ops/backup.py .local/backups/manual-backup
```

每个备份目录包含 PostgreSQL 自定义格式快照、凭据加密密钥和 SHA-256 校验清单。只有完整备份才能解密恢复供应商密码和 2FA 配置；备份包含敏感数据，应复制到受控的外部存储。

恢复会替换当前数据库并中断业务服务，请确认目标实例与备份目录：

```sh
python3 ops/restore.py .local/backups/manual-backup --confirm
```

恢复脚本先校验备份，再停止 Web/API/worker，以单事务恢复数据库，安装原凭据密钥，执行迁移并重建业务服务。恢复失败时业务服务保持停止，应处理错误后重试。恢复完成后检查就绪接口和已知账号。数据库备份需使用相容版本的 PostgreSQL 恢复。

## 私有文件

`.env`、`.local/`、企业 CA、编辑器交换文件、测试截图和构建产物均不进入 Git。核心代码和示例不保存真实服务器地址、人员账号或本机目录。代码提交前可运行：

```sh
python3 ops/check_hygiene.py
# git add 后，读取暂存区内容再次审核。
python3 ops/check_hygiene.py --staged
git diff --cached --check
```

检查脚本识别个人目录、非示例 IPv4、私钥与不应提交的运行文件；允许回环地址、监听通配地址、文档示例网段及容器固定路径。`--staged` 检查实际暂存内容，避免工作区已清理但暂存区仍保留旧内容。第三方服务域名、测试夹具和二进制文件仍需人工审核，不能仅凭自动扫描判断是否包含敏感信息。
