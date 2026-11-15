import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Alert,
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
} from "antd";
import { api, newRequestId, type GroupOption, type PersonOption } from "./api";
import { DateTimeInput, dateTimeRule } from "./DateTimeInput";
import { AnomalyField, TierField, useTierFilters } from "./AccountOptions";
import { MailToolField } from "./MailToolField";
import { fmt, iso, useResource } from "./ui";
import type { AccountFilters } from "./useAccountSelection";

const labels: Record<string, string> = {
  tier: "账号类型",
  capacity: "同时领用人数",
  quota_reset_interval_days: "自动重置周期",
  expires_at: "到期时间",
  mail_tool: "邮箱取码工具",
  enabled: "启用状态",
  group_ids: "可见用户组",
  user_ids: "指定用户",
  quota: "剩余额度",
  reset_at: "下次重置时间",
  health: "异常标记",
  note: "追加备注",
};
const required = [{ required: true, message: "请填写此项" }];
export function BulkAccountEdit({
  accountIds,
  filters,
  groups,
  users,
  onClose,
  onSaved,
}: {
  accountIds: string[];
  filters: AccountFilters;
  groups: GroupOption[];
  users: PersonOption[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const tiers = useTierFilters();
  const { data: tools } = useResource<{
    items: { id: string; name: string }[];
  }>("/mail-tools", 0);
  function describe(key: string, value: unknown): string {
    if (key === "group_ids")
      return (
        (value as string[])
          .map((id) => groups.find((g) => g.id === id)?.name || id)
          .join("、") || "清空用户组"
      );
    if (key === "user_ids")
      return (
        (value as string[])
          .map((id) => users.find((u) => u.id === id)?.display_name || id)
          .join("、") || "清空指定用户"
      );
    if (key === "tier")
      return String(tiers.find((t) => t.value === value)?.label || value);
    if (key === "mail_tool")
      return tools?.items.find((t) => t.id === value)?.name || "不启用自动取码";
    if (key === "enabled") return value ? "启用" : "停用";
    if (key === "expires_at") return value ? fmt(value as string) : "不过期";
    if (key === "reset_at")
      return value ? fmt(value as string) : "清除重置计划";
    if (key === "health") {
      const health = value as {
        action: string;
        note: string;
        categories: string[];
      };
      return health.action === "clear"
        ? "清除异常"
        : `登记异常${health.note ? `：${health.note}` : "（按所选类别）"}`;
    }
    if (value === null) return "沿用全局设置";
    return (
      String(value) +
      (key === "quota" ? "%" : key === "quota_reset_interval_days" ? " 天" : "")
    );
  }
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const [fields, setFields] = useState<string[]>([]);
  const [targetIds, setTargetIds] = useState(accountIds);
  const [pending, setPending] = useState<"preview" | "submit">();
  const [error, setError] = useState("");
  const busy = pending !== undefined;
  const feedback = useRef<HTMLDivElement>(null);
  const inflight = useRef(false);
  const generation = useRef(0);
  const [preview, setPreview] = useState<{
    revision: string;
    changes: Record<string, unknown>;
    request_id: string;
  }>();
  useEffect(() => {
    if (preview || error)
      feedback.current?.scrollIntoView({ block: "nearest" });
  }, [preview, error]);
  function invalidate() {
    generation.current++;
    setPreview(undefined);
    setError("");
  }
  const healthAction = Form.useWatch("health_action", form) || "report";
  function field(name: string, content: ReactNode) {
    const enabled = fields.includes(name);
    return (
      <div
        className={`bulk-edit-field${enabled ? " is-enabled" : ""}`}
        key={name}
      >
        <div className="bulk-edit-field-heading">
          <label htmlFor={`bulk-switch-${name}`}>{labels[name]}</label>
          <Space size={6}>
            <span className="muted small">{enabled ? "修改" : "不修改"}</span>
            <Switch
              id={`bulk-switch-${name}`}
              size="small"
              checked={enabled}
              disabled={busy}
              aria-label={`修改${labels[name]}`}
              onChange={(checked) => {
                invalidate();
                setFields((current) =>
                  checked
                    ? [...current, name]
                    : current.filter((k) => k !== name),
                );
              }}
            />
          </Space>
        </div>
        {enabled && content}
      </div>
    );
  }
  function build(values: any) {
    const changes: Record<string, unknown> = {};
    for (const key of fields) {
      if (key === "health")
        changes.health = {
          action: values.health_action || "report",
          categories: values.categories || [],
          note: values.health_note || "",
        };
      else if (key === "expires_at" || key === "reset_at")
        changes[key] = iso(values[key]);
      else if (key === "group_ids" || key === "user_ids")
        changes[key] = values[key] || [];
      else if (
        ["capacity", "quota_reset_interval_days", "mail_tool"].includes(key)
      )
        changes[key] = values[key] ?? null;
      else if (key === "enabled") changes[key] = values[key] === "true";
      else changes[key] = values[key];
    }
    return changes;
  }
  async function handleError(error: Error & { status?: number }) {
    setError(error.message);
    if (error.status === 409 || error.status === 422) setPreview(undefined);
    if (error.status === 409) {
      try {
        const selection = await api<{ account_ids: string[] }>(
          "/account-selections",
          "POST",
          { account_ids: targetIds, filters },
        );
        setTargetIds(selection.account_ids);
        const removed = targetIds.length - selection.account_ids.length;
        if (removed)
          setError(
            `${error.message}；已移除 ${removed} 个不再匹配的账号，请核对数量后重新预览。`,
          );
      } catch {
        setError(`${error.message}；选择范围核对失败，请取消后重新选择。`);
      }
    }
  }
  async function inspect() {
    if (inflight.current) return;
    inflight.current = true;
    setPending("preview");
    invalidate();
    const token = generation.current;
    try {
      const values = await form.validateFields();
      const changes = build(values);
      const result = await api<{ revision: string }>(
        "/account-bulk-edits/preview",
        "POST",
        { account_ids: targetIds, filters, changes },
      );
      if (token === generation.current)
        setPreview({
          revision: result.revision,
          changes,
          request_id: newRequestId(),
        });
    } catch (e) {
      if (e instanceof Error) await handleError(e);
    } finally {
      inflight.current = false;
      setPending(undefined);
    }
  }
  async function submit() {
    if (!preview || inflight.current) return;
    inflight.current = true;
    setPending("submit");
    setError("");
    try {
      const result = await api<{ count: number }>(
        "/account-bulk-edits",
        "POST",
        {
          account_ids: targetIds,
          filters,
          ...preview,
        },
      );
      onSaved();
      message.success(`已批量修改 ${result.count} 个账号`);
    } catch (e) {
      await handleError(e as Error & { status?: number });
    } finally {
      inflight.current = false;
      setPending(undefined);
    }
  }
  return (
    <Modal
      open
      title="批量编辑账号"
      width={640}
      footer={null}
      destroyOnHidden
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      onCancel={onClose}
      className="bulk-edit-modal"
    >
      <div className="bulk-edit-summary">
        <strong>已选择 {targetIds.length} 个账号</strong>
        <span className="muted">只修改启用的项目</span>
      </div>
      <Form
        form={form}
        layout="vertical"
        disabled={busy}
        onValuesChange={invalidate}
        initialValues={{ enabled: "true", health_action: "report" }}
      >
        <section className="bulk-edit-section">
          <h3>账号配置</h3>
          {field("tier", <TierField label="" />)}
          {field(
            "capacity",
            <Form.Item name="capacity" extra="留空恢复全局人数上限">
              <InputNumber
                min={1}
                max={1000}
                precision={0}
                style={{ width: "100%" }}
              />
            </Form.Item>,
          )}
          {field(
            "quota_reset_interval_days",
            <Form.Item
              name="quota_reset_interval_days"
              extra="留空沿用全局周期；不会更改已有下次重置时间"
            >
              <InputNumber
                min={1}
                max={365}
                precision={0}
                style={{ width: "100%" }}
              />
            </Form.Item>,
          )}
          {field(
            "expires_at",
            <Form.Item
              name="expires_at"
              rules={[dateTimeRule]}
              extra="留空设为不过期"
            >
              <DateTimeInput />
            </Form.Item>,
          )}
          {field("mail_tool", <MailToolField />)}
          {field(
            "enabled",
            <Form.Item name="enabled">
              <Select
                options={[
                  { value: "true", label: "启用" },
                  { value: "false", label: "停用" },
                ]}
              />
            </Form.Item>,
          )}
        </section>
        <section className="bulk-edit-section">
          <h3>可见范围</h3>
          {field(
            "group_ids",
            <Form.Item
              name="group_ids"
              extra="统一替换；清空则移除全部可见用户组"
            >
              <Select
                mode="multiple"
                showSearch
                optionFilterProp="label"
                options={groups.map((g) => ({ value: g.id, label: g.name }))}
              />
            </Form.Item>,
          )}
          {field(
            "user_ids",
            <Form.Item name="user_ids" extra="统一替换；清空则移除全部指定用户">
              <Select
                mode="multiple"
                showSearch
                optionFilterProp="label"
                options={users.map((u) => ({
                  value: u.id,
                  label: `${u.display_name}（${u.username}）`,
                }))}
              />
            </Form.Item>,
          )}
        </section>
        <section className="bulk-edit-section">
          <h3>额度与异常</h3>
          {field(
            "quota",
            <Form.Item name="quota" rules={required}>
              <InputNumber
                min={0}
                max={100}
                precision={0}
                addonAfter="%"
                style={{ width: "100%" }}
              />
            </Form.Item>,
          )}
          {field(
            "reset_at",
            <Form.Item
              name="reset_at"
              rules={[dateTimeRule]}
              extra="留空清除下次重置时间；不修改剩余额度"
            >
              <DateTimeInput />
            </Form.Item>,
          )}
          {field(
            "health",
            <>
              <Form.Item name="health_action">
                <Select
                  options={[
                    { value: "report", label: "登记异常" },
                    { value: "clear", label: "清除异常" },
                  ]}
                />
              </Form.Item>
              {healthAction === "report" && (
                <>
                  <AnomalyField />
                  <Form.Item name="health_note" label="异常描述">
                    <Input.TextArea rows={2} maxLength={2000} />
                  </Form.Item>
                </>
              )}
            </>,
          )}
        </section>
        <section className="bulk-edit-section">
          <h3>情况备注</h3>
          {field(
            "note",
            <Form.Item
              name="note"
              rules={required}
              extra="向每个所选账号追加相同备注，保留历史记录"
            >
              <Input.TextArea rows={2} maxLength={200} showCount />
            </Form.Item>,
          )}
        </section>
      </Form>
      <div ref={feedback} className="bulk-edit-feedback">
        {preview && (
          <Alert
            className="bulk-edit-preview"
            type="info"
            showIcon
            message={`将修改 ${targetIds.length} 个账号`}
            description={
              <>
                <dl className="bulk-edit-changes">
                  {Object.entries(preview.changes).map(([key, value]) => (
                    <div key={key}>
                      <dt>{labels[key]}</dt>
                      <dd>{describe(key, value)}</dd>
                    </div>
                  ))}
                </dl>
                <div>未启用的项目保持原样；数据变化时整批暂停。</div>
              </>
            }
          />
        )}
        {error && (
          <Alert
            className="bulk-edit-preview"
            type="error"
            showIcon
            message={error}
          />
        )}
      </div>
      <div className="dialog-footer">
        <Button disabled={busy} onClick={onClose}>
          取消
        </Button>
        <Button
          aria-label="预览修改"
          loading={pending === "preview"}
          disabled={busy || !fields.length || !targetIds.length}
          onClick={inspect}
        >
          预览修改
        </Button>
        <Button
          type="primary"
          aria-label="确认修改"
          loading={pending === "submit"}
          disabled={busy || !preview}
          onClick={submit}
        >
          确认修改
        </Button>
      </div>
    </Modal>
  );
}
