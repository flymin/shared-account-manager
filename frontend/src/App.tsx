import { useCallback, useEffect, useState } from "react";
import {
  App as AntApp,
  Alert,
  Avatar,
  Button,
  Form,
  Input,
  Layout,
  Menu,
  Spin,
  Tag,
} from "antd";
import {
  AppstoreOutlined,
  KeyOutlined,
  HistoryOutlined,
  DashboardOutlined,
  TeamOutlined,
  SettingOutlined,
  AuditOutlined,
  LogoutOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { api, setCsrf, getSessionVersion, type Person } from "./api";
import { Pool, MyClaims } from "./Accounts";
import { AccountOptionsProvider } from "./AccountOptions";
import {
  AdminAccounts,
  People,
  SystemSettings,
  Dashboard,
  AuditPage,
} from "./Admin";
import { Dialog } from "./ui";
import {
  PASSWORD_HINT,
  PASSWORD_MIN_LENGTH,
  passwordRules,
} from "./passwordPolicy";
const { Header, Sider, Content } = Layout;
const names: Record<string, [string, string]> = {
  pool: ["账号大厅", "查看共享账号状态，选择适合的档位开始使用。"],
  mine: ["我的领用", "领用即使用。及时反馈额度，完成后主动归还。"],
  history: ["领用历史", "每次领用与归还，都有迹可循。"],
  dashboard: ["管理概览", "账号资源、使用情况与待处理事项，一目了然。"],
  accounts: ["账号管理", "维护档位、有效期与可见范围。"],
  people: ["用户与用户组", "让合适的人访问合适的账号。"],
  settings: ["系统设置", "统一配置领用上限与异常恢复规则。"],
  audit: ["操作审计", "查看账号、用户与系统的变更记录。"],
};
export default function App() {
  const [user, setUser] = useState<Person | null>(null),
    [loading, setLoading] = useState(true),
    [epoch, setEpoch] = useState(0),
    [page, setPage] = useState("pool"),
    [passwordOpen, setPasswordOpen] = useState(false),
    [sessionError, setSessionError] = useState("");
  const { message } = AntApp.useApp();
  const refresh = useCallback(() => setEpoch((v) => v + 1), []);
  useEffect(() => {
    let alive = true;
    const version = getSessionVersion();
    api("/auth/me")
      .then((v) => {
        if (alive && version === getSessionVersion()) {
          setCsrf(v.csrf_token);
          setUser(v.user);
          setSessionError("");
        }
      })
      .catch((e) => {
        if (
          alive &&
          version === getSessionVersion() &&
          e.message !== "请先登录" &&
          e.message !== "登录已失效"
        )
          setSessionError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [epoch]);
  useEffect(() => {
    const timer = setInterval(refresh, 15000);
    const expire = () => {
      setUser(null);
      setCsrf("");
      setPasswordOpen(false);
    };
    window.addEventListener("focus", refresh);
    window.addEventListener("session-expired", expire);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("session-expired", expire);
    };
  }, [refresh]);
  useEffect(() => {
    if (user?.role === "user" && !["pool", "mine", "history"].includes(page))
      setPage("pool");
  }, [user?.role, page]);
  async function logout() {
    try {
      await api("/auth/logout", "POST");
      setUser(null);
      setCsrf("");
      setPage("pool");
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  function loggedIn(result: any) {
    setCsrf(result.csrf_token);
    setUser(result.user);
    setSessionError("");
    setEpoch((v) => v + 1);
  }
  if (loading)
    return (
      <div className="splash">
        <Spin size="large" />
      </div>
    );
  if (!user) return <Login onSuccess={loggedIn} error={sessionError} />;
  if (user.must_change_password)
    return (
      <div className="login-page">
        <div className="login-card">
          <Brand />
          <h1>设置您的登录密码</h1>
          <p className="muted">首次登录或密码被重置后，请先修改密码。</p>
          <PasswordForm
            onSuccess={() => {
              setUser(null);
              setCsrf("");
              message.success("密码已更新，请使用新密码登录");
            }}
          />
        </div>
      </div>
    );
  const entries = [
    {
      key: "pool",
      icon: <AppstoreOutlined aria-hidden="true" />,
      label: "账号大厅",
    },
    {
      key: "mine",
      icon: <KeyOutlined aria-hidden="true" />,
      label: "我的领用",
    },
    {
      key: "history",
      icon: <HistoryOutlined aria-hidden="true" />,
      label: "领用历史",
    },
    ...(user.role === "admin"
      ? [
          {
            key: "dashboard",
            icon: <DashboardOutlined aria-hidden="true" />,
            label: "管理概览",
          },
          {
            key: "accounts",
            icon: <SafetyCertificateOutlined aria-hidden="true" />,
            label: "账号管理",
          },
          {
            key: "people",
            icon: <TeamOutlined aria-hidden="true" />,
            label: "用户与用户组",
          },
          {
            key: "settings",
            icon: <SettingOutlined aria-hidden="true" />,
            label: "系统设置",
          },
          {
            key: "audit",
            icon: <AuditOutlined aria-hidden="true" />,
            label: "操作审计",
          },
        ]
      : []),
  ];
  const selected = names[page] || names.pool;
  return (
    <AccountOptionsProvider key={getSessionVersion()} epoch={epoch}>
      <Layout className="app-shell">
        <Sider width={220} className="sidebar" theme="light">
          <Brand />
          <div className="nav-label">工作空间</div>
          <Menu
            mode="inline"
            selectedKeys={[page]}
            items={entries}
            onClick={(e) => setPage(e.key)}
          />
          <div className="sidebar-bottom">
            <span className="status-dot" /> 数据持久化存储
          </div>
        </Sider>
        <Layout>
          <Header className="topbar">
            <div className="breadcrumb">
              <span className="breadcrumb-parent">
                工作空间 <span className="breadcrumb-separator">/</span>
              </span>
              {selected[0]}
            </div>
            <div className="user-menu">
              <Tag color="green">
                {user.role === "admin" ? "管理员" : "普通用户"}
              </Tag>
              <Avatar
                size={30}
                style={{ background: "#dcece6", color: "#195c4d" }}
              >
                {user.display_name.slice(0, 1)}
              </Avatar>
              <span className="user-name">{user.display_name}</span>
              <Button type="text" onClick={() => setPasswordOpen(true)}>
                改密
              </Button>
              <Button
                type="text"
                icon={<LogoutOutlined aria-hidden="true" />}
                onClick={logout}
                aria-label="退出登录"
              />
            </div>
          </Header>
          <nav className="mobile-nav">
            {entries.map((e) => (
              <button
                className={page === e.key ? "selected" : ""}
                key={e.key}
                onClick={() => setPage(e.key)}
              >
                {e.icon}
                {e.label}
              </button>
            ))}
          </nav>
          <Content className="workspace">
            <div className="page-heading">
              <div>
                <div className="eyebrow">ACCOUNT MANAGER</div>
                <h1>{selected[0]}</h1>
                <p>{selected[1]}</p>
              </div>
              <div className="personal-quota">
                <span>我的领用名额</span>
                <div>
                  <strong>{user.claims_used}</strong>
                  <span> / {user.effective_claim_limit}</span>
                </div>
                <small>
                  {Math.max(0, user.effective_claim_limit - user.claims_used)}{" "}
                  个可用名额
                </small>
              </div>
            </div>
            {sessionError && <Alert type="warning" message={sessionError} />}
            {page === "pool" && (
              <Pool user={user} epoch={epoch} refresh={refresh} />
            )}
            {page === "mine" && <MyClaims epoch={epoch} refresh={refresh} />}
            {page === "history" && (
              <MyClaims key="history" epoch={epoch} refresh={refresh} history />
            )}
            {user.role === "admin" && (
              <>
                {page === "dashboard" && (
                  <Dashboard epoch={epoch} refresh={refresh} />
                )}
                {page === "accounts" && (
                  <AdminAccounts user={user} epoch={epoch} refresh={refresh} />
                )}
                {page === "people" && (
                  <People epoch={epoch} refresh={refresh} />
                )}
                {page === "settings" && (
                  <SystemSettings epoch={epoch} refresh={refresh} />
                )}
                {page === "audit" && <AuditPage epoch={epoch} />}
              </>
            )}
          </Content>
          <footer className="footer">账号有序共享，使用状态及时同步。</footer>
        </Layout>
        {passwordOpen && (
          <Dialog
            title="修改登录密码"
            onClose={() => setPasswordOpen(false)}
            onSubmit={async (v) => {
              await api("/auth/password", "PUT", v);
              setUser(null);
              setCsrf("");
              setPasswordOpen(false);
            }}
          >
            <PasswordFields />
          </Dialog>
        )}
      </Layout>
    </AccountOptionsProvider>
  );
}
function Brand() {
  return (
    <div className="brand">
      <span>
        <KeyOutlined aria-hidden="true" />
      </span>
      <div>
        账号管理<small>ACCOUNT MANAGER</small>
      </div>
    </div>
  );
}
function Login({
  onSuccess,
  error,
}: {
  onSuccess: (v: any) => void;
  error: string;
}) {
  const [busy, setBusy] = useState(false),
    [loginError, setLoginError] = useState("");
  return (
    <div className="login-page">
      <div className="login-story">
        <div className="eyebrow">A CLEARER WAY TO SHARE</div>
        <h1>
          共享账号，
          <br />
          有序使用。
        </h1>
        <p>从领用到归还，每一份额度都有清晰的状态。</p>
        <div className="story-card">
          <div>
            <span className="status-dot" />
            账号资源池
          </div>
          <div className="story-bars">
            <i />
            <i />
            <i />
          </div>
          <span>清晰可见 · 随时同步 · 安心协作</span>
        </div>
      </div>
      <div className="login-card">
        <Brand />
        <h2>欢迎回来</h2>
        <p className="muted">使用管理员为您创建的账号登录。</p>
        {(error || loginError) && (
          <Alert type="error" showIcon message={loginError || error} />
        )}
        <Form
          layout="vertical"
          onFinish={async (v) => {
            setBusy(true);
            setLoginError("");
            try {
              onSuccess(await api("/auth/login", "POST", v));
            } catch (e) {
              setLoginError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input
              size="large"
              autoComplete="username"
              placeholder="输入您的用户名"
            />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password
              size="large"
              autoComplete="current-password"
              placeholder="输入登录密码"
            />
          </Form.Item>
          <Button
            size="large"
            block
            type="primary"
            htmlType="submit"
            loading={busy}
          >
            登录工作空间
          </Button>
        </Form>
        <p className="login-help">无法登录？请联系系统管理员。</p>
      </div>
    </div>
  );
}
function PasswordFields() {
  return (
    <>
      <Form.Item
        name="current_password"
        label="当前密码"
        rules={[{ required: true, message: "请输入当前密码" }]}
      >
        <Input.Password autoComplete="current-password" />
      </Form.Item>
      <Form.Item
        name="new_password"
        label={`新密码（至少 ${PASSWORD_MIN_LENGTH} 位）`}
        rules={passwordRules()}
        extra={PASSWORD_HINT}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
    </>
  );
}
function PasswordForm({ onSuccess }: { onSuccess: () => void }) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <>
      {error && <Alert type="error" message={error} />}
      <Form
        layout="vertical"
        onFinish={async (v) => {
          setBusy(true);
          try {
            await api("/auth/password", "PUT", v);
            onSuccess();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <PasswordFields />
        <Button type="primary" block htmlType="submit" loading={busy}>
          更新密码并重新登录
        </Button>
      </Form>
    </>
  );
}
