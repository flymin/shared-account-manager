import { useEffect, useRef, useState } from "react";
import { Alert, App, Button, Space, Tag } from "antd";
import { api, ApiError, getSessionVersion } from "./api";
import { CopyButton } from "./ui";

type Kind = "service";
type Config = {
  configured: boolean;
  version: number;
  updated_at: string | null;
};
type Status = {
  server_time: string;
  email_available: boolean;
  email_tool_revision: string | null;
  email_config_version: number;
  email_unavailable_reason: string | null;
  two_factor: Record<Kind, Config>;
  email: {
    owner: string;
    mine: boolean;
    deadline: string;
    id: string | null;
  } | null;
};
type Run = {
  id: string;
  status:
    | "pending"
    | "reading"
    | "found"
    | "timed_out"
    | "cancelled"
    | "failed";
  deadline: string;
  server_time: string;
  code?: string;
  received_at?: string;
  error?: string | null;
};
type Totp = {
  code: string;
  valid_until: string;
  server_time: string;
  period: number;
  version: number;
};
const active = (run: Run | null) =>
  run && ["pending", "reading"].includes(run.status);
const countdown = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
export function receivedTime(value: string) {
  const date = new Date(value);
  const pad = (v: number) => String(v).padStart(2, "0");
  const offset = -date.getTimezoneOffset();
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}（UTC${offset >= 0 ? "+" : "-"}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}）`;
}

export function Verification({
  accountId,
  manageTwoFactor = false,
}: {
  accountId: string;
  manageTwoFactor?: boolean;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(Date.now());
  const alive = useRef(false),
    runId = useRef<string | null>(null);
  const offset = useRef(0),
    currentRun = useRef<Run | null>(null);
  const session = useRef(getSessionVersion());
  const generation = useRef(0);
  const lifecycle = useRef(0);
  const mailConfigVersion = useRef<string | null>(null);
  const base = `/accounts/${accountId}`;
  const canUpdate = () =>
    alive.current && session.current === getSessionVersion();
  const seconds = (deadline: string) =>
    Math.max(0, Math.ceil((Date.parse(deadline) - clock) / 1000));
  const acceptRun = (value: Run) => {
    offset.current = Date.parse(value.server_time) - Date.now();
    currentRun.current = value;
    setRun(value);
  };
  const cancelId = (id: string) =>
    api(`${base}/email-code-runs/${id}`, "DELETE", undefined, {
      keepalive: true,
    }).catch(() => {});

  useEffect(() => {
    alive.current = true;
    session.current = getSessionVersion();
    let polling = false,
      nextStatus = 0;
    let controller = new AbortController();
    const refresh = async () => {
      if (!canUpdate() || polling) return;
      polling = true;
      const cycle = lifecycle.current;
      try {
        if (Date.now() >= nextStatus) {
          const data = await api<Status>(
            `${base}/verification`,
            "GET",
            undefined,
            { signal: controller.signal },
          );
          if (!canUpdate() || cycle !== lifecycle.current) return;
          offset.current = Date.parse(data.server_time) - Date.now();
          setStatus((previous) => ({
            ...data,
            two_factor: {
              service:
                previous &&
                previous.two_factor.service.version >
                  data.two_factor.service.version
                  ? previous.two_factor.service
                  : data.two_factor.service,
            },
          }));
          setError("");
          const mailVersion = `${data.email_config_version}:${data.email_tool_revision || ""}`;
          const mailChanged =
            mailConfigVersion.current !== null &&
            mailConfigVersion.current !== mailVersion;
          mailConfigVersion.current = mailVersion;
          if ((!data.email_available || mailChanged) && runId.current) {
            generation.current += 1;
            void cancelId(runId.current);
            runId.current = null;
            currentRun.current = null;
            setRun(null);
            setStarting(false);
          }
          nextStatus = Date.now() + 3000;
        }
        const id = runId.current;
        if (id && active(currentRun.current)) {
          const data = await api<Run>(
            `${base}/email-code-runs/${id}`,
            "GET",
            undefined,
            { signal: controller.signal },
          );
          if (
            canUpdate() &&
            cycle === lifecycle.current &&
            runId.current === id
          )
            acceptRun(data);
        }
      } catch (e) {
        if (
          !canUpdate() ||
          cycle !== lifecycle.current ||
          (e as Error).name === "AbortError"
        )
          return;
        setError((e as Error).message);
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
          generation.current += 1;
          lifecycle.current += 1;
          setStarting(false);
          setStatus(null);
          setRun(null);
          currentRun.current = null;
          runId.current = null;
        }
      } finally {
        polling = false;
      }
    };
    const leave = () => {
      generation.current += 1;
      lifecycle.current += 1;
      controller.abort();
      if (alive.current) {
        setRun(null);
        setStarting(false);
        setStatus(null);
      }
      alive.current = false;
      if (runId.current && session.current === getSessionVersion())
        void cancelId(runId.current);
      runId.current = null;
      currentRun.current = null;
      mailConfigVersion.current = null;
    };
    const resume = () => {
      if (session.current !== getSessionVersion()) return;
      controller = new AbortController();
      alive.current = true;
      polling = false;
      nextStatus = 0;
      void refresh();
    };
    void refresh();
    const timer = setInterval(() => {
      const stamp = Date.now() + offset.current;
      setClock(stamp);
      const value = currentRun.current;
      if (value && Date.parse(value.deadline) <= stamp) {
        currentRun.current = {
          ...value,
          status: "timed_out",
          code: undefined,
          received_at: undefined,
        };
        setRun(currentRun.current);
      }
      void refresh();
    }, 1000);
    window.addEventListener("pagehide", leave);
    window.addEventListener("pageshow", resume);
    window.addEventListener("session-changed", leave);
    window.addEventListener("focus", refresh);
    return () => {
      alive.current = false;
      controller.abort();
      clearInterval(timer);
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("session-changed", leave);
      window.removeEventListener("focus", refresh);
      leave();
    };
  }, [accountId]);

  async function start() {
    const token = ++generation.current;
    // randomUUID is unavailable on some plain-HTTP mobile deployments.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map((v) => v.toString(16).padStart(2, "0")).join("");
    const id = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    runId.current = id;
    currentRun.current = null;
    setRun(null);
    setStarting(true);
    setError("");
    try {
      const data = await api<Run>(`${base}/email-code-runs`, "POST", { id });
      if (!canUpdate() || generation.current !== token) {
        if (session.current === getSessionVersion()) void cancelId(id);
        return;
      }
      acceptRun(data);
    } catch (e) {
      if (session.current === getSessionVersion()) void cancelId(id);
      if (canUpdate() && generation.current === token) {
        setError((e as Error).message);
        runId.current = null;
      }
    } finally {
      if (canUpdate() && generation.current === token) setStarting(false);
    }
  }
  async function cancel() {
    const id = runId.current || status?.email?.id;
    if (!id) return;
    try {
      const data = await api<Run>(`${base}/email-code-runs/${id}`, "DELETE");
      if (!canUpdate() || (runId.current && runId.current !== id)) return;
      generation.current += 1;
      acceptRun(data);
      runId.current = null;
      setStatus((value) => (value ? { ...value, email: null } : value));
    } catch (e) {
      if (canUpdate()) setError((e as Error).message);
    }
  }
  const busy =
    status?.email && seconds(status.email.deadline) > 0 && !active(run);
  return (
    <div className="verification" aria-label="账号验证码">
      <div className="verification-section">
        <div className="verification-heading">
          <strong>邮箱验证码</strong>
          <Space className="verification-actions" size={6} wrap>
            {status && !status.email_available ? (
              <span className="muted">
                {status.email_unavailable_reason || "自动取码不可用"}
              </span>
            ) : (
              <Button
                aria-label="获取邮箱验证码"
                onClick={start}
                loading={starting}
                disabled={!status?.email_available || !!active(run) || !!busy}
              >
                获取验证码
              </Button>
            )}
            {active(run) && (
              <Button aria-label="取消获取" onClick={cancel}>
                取消
              </Button>
            )}
            {!active(run) && status?.email?.mine && (
              <Button aria-label="取消已有获取" onClick={cancel}>
                取消
              </Button>
            )}
          </Space>
        </div>
        {error && <Alert type="warning" message={error} />}
        {busy && (
          <Alert
            type="info"
            message={`${status.email!.owner} 正在获取，请暂勿使用 · ${countdown(seconds(status.email!.deadline))}`}
          />
        )}
        {active(run) && (
          <span className="muted">
            等待新邮件 · 剩余 {countdown(seconds(run!.deadline))}
          </span>
        )}
        {status?.email_available &&
          run?.status === "found" &&
          run.code &&
          run.received_at &&
          seconds(run.deadline) > 0 && (
            <div className="verification-result">
              <div>
                <code className="verification-code">{run.code}</code>
                <CopyButton value={run.code} label="邮箱验证码" />
              </div>
              <time dateTime={run.received_at}>
                接收于 {receivedTime(run.received_at)}
              </time>
            </div>
          )}
        {run?.status === "timed_out" && (
          <span className="muted">本轮已结束，可重新获取验证码。</span>
        )}
        {run?.status === "failed" && (
          <Alert type="warning" message={run.error || "获取失败，请重试"} />
        )}
        {run?.status === "cancelled" && (
          <span className="muted">已取消获取。</span>
        )}
      </div>
      {status &&
        (["service"] as Kind[]).map((kind) => (
          <TwoFactor
            key={kind}
            kind={kind}
            accountId={accountId}
            config={status.two_factor[kind]}
            canManage={manageTwoFactor}
            onConfigured={(config) =>
              setStatus((value) =>
                value && config.version >= value.two_factor[kind].version
                  ? {
                      ...value,
                      two_factor: { ...value.two_factor, [kind]: config },
                    }
                  : value,
              )
            }
          />
        ))}
    </div>
  );
}

function TwoFactor({
  kind,
  accountId,
  config,
  canManage,
  onConfigured,
}: {
  kind: Kind;
  accountId: string;
  config: Config;
  canManage: boolean;
  onConfigured: (config: Config) => void;
}) {
  const title = "账号 2FA";
  const [data, setData] = useState<Totp | null>(null),
    [error, setError] = useState("");
  const [saving, setSaving] = useState(false),
    [end, setEnd] = useState(0),
    [clock, setClock] = useState(Date.now());
  const input = useRef<HTMLInputElement>(null),
    alive = useRef(false),
    token = useRef(0),
    version = useRef(config.version);
  const session = useRef(getSessionVersion()),
    current = useRef<Totp | null>(null),
    offset = useRef(0);
  const { modal, message } = App.useApp();
  const popup = useRef<{ destroy: () => void } | null>(null);
  const base = `/accounts/${accountId}/two-factor/${kind}`;
  const valid = () => alive.current && session.current === getSessionVersion();
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      token.current++;
      popup.current?.destroy();
    };
  }, []);
  useEffect(() => {
    if (version.current !== config.version) {
      version.current = config.version;
      token.current++;
      current.current = null;
      setData(null);
      setError("");
    }
    if (!config.configured) setEnd(0);
  }, [config.version, config.configured]);
  useEffect(() => {
    if (!end || !config.configured) return;
    let polling = false;
    const controller = new AbortController();
    const refresh = async () => {
      const stamp = Date.now();
      setClock(stamp + offset.current);
      if (stamp >= end) {
        setEnd(0);
        setData(null);
        current.current = null;
        return;
      }
      if (polling || !valid()) return;
      const value = current.current;
      if (value && Date.parse(value.valid_until) > stamp + offset.current + 200)
        return;
      polling = true;
      const attempt = token.current;
      try {
        const result = await api<Totp>(`${base}/code`, "GET", undefined, {
          signal: controller.signal,
        });
        if (
          !valid() ||
          attempt !== token.current ||
          Date.now() >= end ||
          result.version !== version.current
        )
          return;
        offset.current = Date.parse(result.server_time) - Date.now();
        current.current = result;
        setData(result);
        setClock(Date.now() + offset.current);
        setError("");
      } catch (e) {
        if (
          !valid() ||
          attempt !== token.current ||
          controller.signal.aborted ||
          (e as Error).name === "AbortError"
        )
          return;
        setData(null);
        current.current = null;
        setError((e as Error).message);
        if (e instanceof ApiError && [401, 403, 404].includes(e.status))
          setEnd(0);
      } finally {
        polling = false;
      }
    };
    const stop = () => {
      setEnd(0);
      setData(null);
      current.current = null;
      token.current++;
    };
    void refresh();
    const timer = setInterval(refresh, 500);
    window.addEventListener("focus", refresh);
    window.addEventListener("pagehide", stop);
    return () => {
      controller.abort();
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pagehide", stop);
    };
  }, [end, accountId, kind, config.version, config.configured]);
  function stopRefresh() {
    token.current++;
    current.current = null;
    setData(null);
    setEnd(0);
    setError("");
  }
  async function upload(file: File) {
    if (!canManage || saving) return;
    if (file.size > 1024 * 1024) {
      message.error("二维码图片不得超过 1 MiB");
      return;
    }
    stopRefresh();
    setSaving(true);
    try {
      const value = await api<Config>(
        `${base}?version=${config.version}`,
        "PUT",
        file,
        { binary: true },
      );
      if (!valid()) return;
      onConfigured(value);
      message.success(`${title} 配置已更新`);
    } catch (e) {
      if (valid()) setError((e as Error).message);
    } finally {
      if (valid()) setSaving(false);
    }
  }
  function remove() {
    popup.current = modal.confirm({
      title: `取消${title}设置？`,
      content:
        "将清除本系统保存的密钥，所有领用者将无法获取验证码。此操作不会关闭 目标服务上的两步验证。",
      okText: "取消设置",
      okButtonProps: { danger: true },
      cancelText: "保留设置",
      onOk: async () => {
        if (!canManage || saving) return;
        stopRefresh();
        setSaving(true);
        try {
          const value = await api<Config>(
            `${base}?version=${config.version}`,
            "DELETE",
          );
          if (!valid()) return;
          onConfigured(value);
          message.success(`${title} 设置已取消`);
        } catch (e) {
          if (valid()) setError((e as Error).message);
        } finally {
          if (valid()) setSaving(false);
        }
      },
    });
  }
  function choose(file?: File) {
    if (!file || !canManage || saving) return;
    if (!config.configured) {
      void upload(file);
      return;
    }
    popup.current = modal.confirm({
      title: `替换${title}配置？`,
      content: "将影响此账号的所有领用者，请确认二维码来自当前账号。",
      okText: "替换",
      cancelText: "取消",
      onOk: () => upload(file),
    });
  }
  const visible =
    config.configured &&
    data &&
    data.version === config.version &&
    Date.parse(data.valid_until) > clock &&
    Date.now() < end;
  return (
    <div className="verification-section">
      <div className="verification-heading">
        <strong>{title}</strong>
        <Space className="verification-actions" size={6} wrap>
          {config.configured ? (
            <Button
              aria-label={`显示 ${title}`}
              disabled={saving}
              onClick={() => {
                token.current++;
                current.current = null;
                setData(null);
                setEnd(Date.now() + 300000);
              }}
            >
              显示
            </Button>
          ) : (
            <Tag>未设置</Tag>
          )}
          {canManage && (
            <>
              <Button loading={saving} onClick={() => input.current?.click()}>
                {config.configured ? "更新二维码" : "上传二维码"}
              </Button>
              {config.configured && (
                <Button danger disabled={saving} onClick={remove}>
                  取消设置
                </Button>
              )}
            </>
          )}
        </Space>
        {canManage && (
          <input
            ref={input}
            hidden
            type="file"
            accept="image/png,image/jpeg,image/webp"
            aria-label={`上传${title}二维码`}
            onChange={(e) => {
              choose(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        )}
      </div>
      {error && <Alert type="warning" message={error} />}
      {!!end && config.configured && (
        <div className="verification-result">
          <div>
            {visible ? (
              <>
                <code className="verification-code">{data.code}</code>
                <CopyButton value={data.code} label={title} />
                <small className="muted">
                  {Math.max(
                    0,
                    Math.ceil((Date.parse(data.valid_until) - clock) / 1000),
                  )}{" "}
                  秒后更新
                </small>
              </>
            ) : (
              <span className="muted">正在获取…</span>
            )}
          </div>
          <small className="muted">
            自动更新剩余{" "}
            {countdown(Math.max(0, Math.ceil((end - Date.now()) / 1000)))}
          </small>
        </div>
      )}
    </div>
  );
}
