import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  App,
  Alert,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Tag,
  Timeline,
} from "antd";
import {
  CopyOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import timezone from "dayjs/plugin/timezone";
import {
  api,
  ApiError,
  getSessionVersion,
  type Account,
  type Audit,
} from "./api";
import { accountStatus, ACCOUNT_STATUSES } from "./accountList";
import { Verification } from "./Verification";
dayjs.extend(utc);
dayjs.extend(timezone);
export const fmt = (v?: string | null) =>
  v ? dayjs(v).tz("Asia/Shanghai").format("YYYY-MM-DD HH:mm") : "未设置";
export const localDate = (v?: string | null) =>
  v ? dayjs(v).tz("Asia/Shanghai").format("YYYY-MM-DDTHH:mm") : "";
export const iso = (v?: string) =>
  v ? dayjs.tz(v, "Asia/Shanghai").toISOString() : null;
export function useResource<T>(path: string, epoch: number) {
  const [sessionVersion, setSessionVersion] = useState(getSessionVersion);
  const [result, setResult] = useState<{
    path: string;
    sessionVersion: number;
    data?: T;
    error: string;
    errorStatus?: number;
  }>({ path: "", sessionVersion: -1, error: "" });
  useEffect(() => {
    const changed = () => {
      const next = getSessionVersion();
      setResult({ path: "", sessionVersion: next, error: "" });
      setSessionVersion(next);
    };
    window.addEventListener("session-changed", changed);
    if (sessionVersion !== getSessionVersion()) changed();
    return () => window.removeEventListener("session-changed", changed);
  }, [sessionVersion]);
  useEffect(() => {
    let alive = true;
    const version = getSessionVersion();
    const controller = new AbortController();
    api<T>(path, "GET", undefined, { signal: controller.signal })
      .then((v) => {
        if (alive && version === getSessionVersion()) {
          setResult({ path, sessionVersion: version, data: v, error: "" });
        }
      })
      .catch((e) => {
        if (alive && version === getSessionVersion() && e.name !== "AbortError")
          setResult((current) => ({
            path,
            sessionVersion: version,
            data:
              current.path === path &&
              current.sessionVersion === version &&
              !(e instanceof ApiError && [401, 403, 404].includes(e.status))
                ? current.data
                : undefined,
            error: e.message,
            errorStatus: e instanceof ApiError ? e.status : undefined,
          }));
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [path, epoch, sessionVersion]);
  const current =
    result.path === path && result.sessionVersion === getSessionVersion();
  return {
    data: current ? result.data : undefined,
    error: current ? result.error : "",
    errorStatus: current ? result.errorStatus : undefined,
  };
}
export function Health({ a }: { a: Account }) {
  const status = accountStatus(a);
  return (
    <Space className="account-health" size={4} wrap>
      {a.disabled && <Tag color="default">已停用</Tag>}
      <Tag
        color={
          status === "normal"
            ? "success"
            : status === "abnormal"
              ? "error"
              : "warning"
        }
        icon={
          status === "normal" ? (
            <CheckCircleOutlined aria-hidden="true" />
          ) : status === "abnormal" ? (
            <WarningOutlined aria-hidden="true" />
          ) : (
            <ClockCircleOutlined aria-hidden="true" />
          )
        }
      >
        {ACCOUNT_STATUSES.find((s) => s.value === status)!.label}
      </Tag>
      {a.expires_at && dayjs(a.expires_at).isBefore(dayjs()) && (
        <Tag>已过期</Tag>
      )}
    </Space>
  );
}
export function Quota({
  a,
  showReset = true,
}: {
  a: Account;
  showReset?: boolean;
}) {
  return (
    <div className="quota">
      <div>
        <strong>{a.quota === null ? "—" : `${a.quota}%`}</strong>
        <span className="muted">剩余</span>
        {a.quota_source === "system" && <Tag bordered={false}>系统重置</Tag>}
      </div>
      <div className="quota-track">
        <i
          style={{
            width: `${a.quota ?? 0}%`,
            background: a.quota_depleted ? "#d98635" : undefined,
          }}
        />
      </div>
      {showReset && (
        <small className="muted">
          {a.reset_at ? `下次重置 ${fmt(a.reset_at)}` : "重置时间未设置"}
        </small>
      )}
    </div>
  );
}
export function CopyButton({ value, label }: { value: string; label: string }) {
  const { message, modal } = App.useApp();
  const popup = useRef<{ destroy: () => void } | null>(null);
  const alive = useRef(true);
  const currentValue = useRef(value);
  currentValue.current = value;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      popup.current?.destroy();
    };
  }, []);
  useEffect(() => {
    popup.current?.destroy();
  }, [value]);
  async function copy() {
    let copied = false;
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(value);
        copied = true;
      }
    } catch {
      /* fallback below */
    }
    if (!alive.current || currentValue.current !== value) return;
    if (!copied) {
      const input = document.createElement("textarea");
      input.value = value;
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      try {
        copied = document.execCommand("copy");
      } catch {
        /* manual fallback */
      } finally {
        input.remove();
      }
    }
    if (copied) message.success(`${label}已复制`);
    else
      popup.current = modal.info({
        title: "请手动复制",
        content: (
          <Input.TextArea
            aria-label="手动复制内容"
            readOnly
            value={value}
            onFocus={(e) => e.target.select()}
          />
        ),
        okText: "完成",
      });
  }
  return (
    <Button
      size="small"
      type="text"
      icon={<CopyOutlined aria-hidden="true" />}
      aria-label={`复制${label}`}
      onClick={copy}
    />
  );
}
export function Credentials({
  accountId,
  epoch,
  manageTwoFactor = false,
}: {
  accountId: string;
  epoch: number;
  manageTwoFactor?: boolean;
}) {
  const { data, error, errorStatus } = useResource<{
    email: string;
    password: string;
    auth_password: string;
  }>(`/accounts/${accountId}/credentials`, epoch);
  if (
    error &&
    (!data || (errorStatus && [401, 403, 404].includes(errorStatus)))
  )
    return <Alert type="warning" message="凭据暂不可用" description={error} />;
  if (!data) return <div className="muted">正在获取凭据…</div>;
  return (
    <div className="credentials">
      {error && (
        <Alert type="warning" message="凭据刷新暂时失败" description={error} />
      )}
      {[
        ["账号", data.email],
        ["账号密码", data.password],
        ["邮箱密码", data.auth_password],
      ].map(([label, value]) => (
        <div className="credential-row" key={label}>
          <span>{label}</span>
          <code>{value}</code>
          <CopyButton value={value} label={label} />
        </div>
      ))}
      <div className="credential-footer">
        <span>复制完整三段格式</span>
        <CopyButton
          label="完整账号"
          value={`${data.email}----${data.password}----${data.auth_password}`}
        />
      </div>
      <Verification
        key={`${accountId}:${getSessionVersion()}`}
        accountId={accountId}
        manageTwoFactor={manageTwoFactor}
      />
    </div>
  );
}
const eventNames: Record<string, string> = {
  quota_updated: "额度更新",
  quota_reset: "额度自动重置",
  health_reported: "异常反馈",
  health_cleared: "确认恢复",
  health_possible: "可能恢复",
  claimed: "领用账号",
  returned: "归还账号",
  claim_invalidated: "管理员回收 / 删除",
  account_created: "登记账号",
  account_updated: "修改账号",
  account_deleted: "删除账号",
  account_disabled: "停用账号",
  account_enabled: "恢复启用账号",
  user_created: "创建用户",
  user_updated: "修改用户",
  user_deleted: "删除用户",
  group_created: "创建用户组",
  group_updated: "修改用户组",
  group_deleted: "删除用户组",
  settings_updated: "修改系统设置",
  password_changed: "修改登录密码",
  two_factor_updated: "更新 2FA 配置",
  two_factor_removed: "取消 2FA 配置",
  email_code_started: "获取邮箱验证码",
};
const fieldNames: Record<string, string> = {
  tier: "档位",
  capacity: "人数上限",
  expires_at: "过期时间",
  quota_reset_interval_days: "额度重置间隔",
  password: "密码",
  auth_password: "邮箱密码",
  mail_tool: "邮箱取码工具",
  mail_backend: "邮箱取码工具",
  group_ids: "用户组",
  user_ids: "指定用户",
  display_name: "姓名",
  role: "角色",
  claim_limit: "领用上限",
};
export function eventText(e: Audit) {
  const d = e.details;
  const parts: string[] = [];
  if (d.quota !== undefined) parts.push(`剩余 ${d.quota}%`);
  if (d.reset_at) parts.push(`重置 ${fmt(d.reset_at)}`);
  if (d.next_reset_at) parts.push(`下次重置 ${fmt(d.next_reset_at)}`);
  if (d.categories?.length) parts.push(d.categories.join("、"));
  if (d.note) parts.push(d.note);
  if (d.reason)
    parts.push(
      d.reason === "cooldown"
        ? "异常冷却结束"
        : d.reason === "observation"
          ? "领用观察期结束"
          : d.reason,
    );
  if (d.kind)
    parts.push(
      (
        {
          normal: "正常归还",
          abnormal: "异常归还",
          acknowledge: "确认归还",
          user_deleted: "删除用户自动归还",
          revoked: "已回收",
          deleted: "已删除",
        } as Record<string, string>
      )[d.kind] || d.kind,
    );
  if (d.fields)
    parts.push(d.fields.map((f: string) => fieldNames[f] || f).join("、"));
  if (d.group_name && d.membership)
    parts.push(
      `${d.membership === "added" ? "加入" : "退出"}用户组 ${d.group_name}`,
    );
  if (d.username || d.name) parts.push(d.username || d.name);
  if (d.target) parts.push(d.target === "mail" ? "邮箱 2FA" : "账号 2FA");
  if (e.kind === "settings_updated")
    parts.push(
      `账号人数 ${d.account_capacity} · 用户名额 ${d.user_claim_limit} · 观察 ${d.observation_hours}h · 冷却 ${d.cooldown_hours}h${d.quota_reset_interval_days !== undefined ? ` · 额度重置 ${d.quota_reset_interval_days}天` : ""}`,
    );
  return parts.join(" · ");
}
export function Events({ events }: { events: Audit[] }) {
  if (!events.length)
    return (
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无记录" />
    );
  return (
    <Timeline
      items={events.map((e) => ({
        children: (
          <div>
            <strong>{eventNames[e.kind] || e.kind}</strong>
            {e.account_email && (
              <p className="event-detail">账号：{e.account_email}</p>
            )}
            {e.target_username && (
              <p className="event-detail">
                用户：{e.target_display_name}（{e.target_username}）
              </p>
            )}
            <p className="event-detail">{eventText(e)}</p>
            <small className="muted">
              {e.actor} · {fmt(e.created_at)}
            </small>
          </div>
        ),
      }))}
    />
  );
}
export function EventHistory({
  accountId,
  epoch,
}: {
  accountId: string;
  epoch: number;
}) {
  const [events, setEvents] = useState<Audit[]>([]),
    [more, setMore] = useState(true),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    api<Audit[]>(`/accounts/${accountId}/events`)
      .then((e) => {
        if (alive) {
          setEvents(e);
          setMore(e.length === 50);
          setError("");
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [accountId, epoch]);
  return (
    <>
      {error && <Alert type="error" message={error} />}
      <Events events={events} />
      {more && (
        <Button
          onClick={() =>
            api<Audit[]>(
              `/accounts/${accountId}/events?before=${events.at(-1)?.id}`,
            )
              .then((e) => {
                setEvents((v) => [...v, ...e]);
                setMore(e.length === 50);
              })
              .catch((e) => setError(e.message))
          }
        >
          加载更多
        </Button>
      )}
    </>
  );
}
export function Dialog({
  title,
  initial,
  onSubmit,
  onClose,
  children,
  okText = "保存",
}: {
  title: string;
  initial?: any;
  onSubmit: (values: any) => Promise<unknown>;
  onClose: () => void;
  children: ReactNode;
  okText?: string;
}) {
  const [form] = Form.useForm(),
    [saving, setSaving] = useState(false);
  const { message } = App.useApp();
  return (
    <Modal
      open
      title={title}
      onCancel={onClose}
      destroyOnHidden
      footer={null}
      width={560}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={initial}
        onFinish={async (values) => {
          setSaving(true);
          try {
            await onSubmit(values);
            onClose();
            message.success("操作成功");
          } catch (e) {
            message.error((e as Error).message);
          } finally {
            setSaving(false);
          }
        }}
      >
        {children}
        <div className="dialog-footer">
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" htmlType="submit" loading={saving}>
            {okText}
          </Button>
        </div>
      </Form>
    </Modal>
  );
}
