import { useRef, useState } from "react";
import { Alert, App, Button, Form, Modal, Space } from "antd";
import { api, newRequestId, type GroupOption } from "./api";
import { DateTimeInput, dateTimeRule } from "./DateTimeInput";
import { fmt, iso, localDate } from "./ui";
import { useTierFilters } from "./AccountOptions";
import { ACCOUNT_STATUSES, DISABLED_ACCOUNT_STATUS } from "./accountList";

type Filters = {
  q: string;
  tier: string | null;
  group_id: string | null;
  statuses: string;
};
type Counts = {
  matched_count: number;
  eligible_count: number;
  skipped_count: number;
};

export function BulkQuotaReset({
  filters,
  groups,
  onClose,
  refresh,
}: {
  filters: Filters;
  groups: GroupOption[];
  onClose: () => void;
  refresh: () => void;
}) {
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const tiers = useTierFilters();
  const [pending, setPending] = useState<"preview" | "submit">();
  const busy = pending !== undefined;
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<{
    counts: Counts;
    occurred_at: string;
    request_id: string;
  }>();
  const generation = useRef(0);
  const inFlight = useRef(false);
  const statuses = [...ACCOUNT_STATUSES, DISABLED_ACCOUNT_STATUS]
    .filter((s) => filters.statuses.split(",").includes(s.value))
    .map((s) => s.label)
    .join("、");
  async function inspect() {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending("preview");
    setError("");
    setPreview(undefined);
    try {
      const values = await form.validateFields();
      const current = ++generation.current;
      const occurred_at = iso(values.occurred_at)!;
      const counts = await api<Counts>(
        "/account-quota-resets/preview",
        "POST",
        { occurred_at, filters },
      );
      if (current === generation.current)
        setPreview({ counts, occurred_at, request_id: newRequestId() });
    } catch (e) {
      if (e instanceof Error) setError(e.message);
    } finally {
      inFlight.current = false;
      setPending(undefined);
    }
  }
  async function submit() {
    if (!preview || inFlight.current) return;
    inFlight.current = true;
    setPending("submit");
    setError("");
    try {
      const result = await api<Counts & { reset_count: number }>(
        "/account-quota-resets",
        "POST",
        {
          occurred_at: preview.occurred_at,
          filters,
          request_id: preview.request_id,
        },
      );
      refresh();
      onClose();
      message.success(
        `已重置 ${result.reset_count} 个账号，保留 ${result.skipped_count} 个账号`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inFlight.current = false;
      setPending(undefined);
    }
  }
  return (
    <Modal
      open
      title="一键重置额度"
      width={560}
      footer={null}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      onCancel={onClose}
      destroyOnHidden
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="作用于全部匹配账号，跨全部页面"
          description={
            <>
              <div>
                邮箱：{filters.q || "不限"} · 类型：
                {tiers.find((t) => t.value === filters.tier)?.label ||
                  filters.tier ||
                  "全部类型"}
              </div>
              <div>
                用户组：
                {groups.find((g) => g.id === filters.group_id)?.name ||
                  "全部用户组"}
              </div>
              <div>状态：{statuses || "未选择任何状态"}</div>
            </>
          }
        />
        <Form
          form={form}
          layout="vertical"
          initialValues={{ occurred_at: localDate(new Date().toISOString()) }}
          onValuesChange={() => {
            generation.current++;
            setPreview(undefined);
            setError("");
          }}
        >
          <Form.Item
            name="occurred_at"
            label="实际重置发生时间"
            rules={[
              { required: true, message: "请填写实际重置发生时间" },
              dateTimeRule,
            ]}
            extra="仅处理已登记下次重置时间、且额度更新时间严格早于此时间的账号。"
          >
            <DateTimeInput disabled={busy} />
          </Form.Item>
        </Form>
        <p className="muted small" style={{ margin: 0 }}>
          重置后剩余额度为
          100%；下次重置沿用账号或全局周期。无额度更新记录、未设置下次重置时间及已有较新记录的账号均保留。预览仅供参考，提交时重新检查。
        </p>
        {preview && (
          <Alert
            type="warning"
            showIcon
            message={`匹配 ${preview.counts.matched_count} 个 · 将重置 ${preview.counts.eligible_count} 个 · 保留 ${preview.counts.skipped_count} 个`}
            description={`实际发生：${fmt(preview.occurred_at)}；额度更新人显示“一键重置”。`}
          />
        )}
        {error && <Alert type="error" message={error} showIcon />}
        <div className="dialog-footer">
          <Button disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button
            aria-label="预览范围"
            loading={pending === "preview"}
            disabled={busy}
            onClick={inspect}
          >
            预览范围
          </Button>
          <Button
            type="primary"
            aria-label="确认重置"
            loading={pending === "submit"}
            disabled={!preview || !preview.counts.eligible_count}
            onClick={submit}
          >
            确认重置
          </Button>
        </div>
      </Space>
    </Modal>
  );
}
