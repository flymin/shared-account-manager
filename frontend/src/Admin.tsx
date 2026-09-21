import { DateTimeInput, dateTimeRule } from "./DateTimeInput";
import { useState } from "react";
import {
  PASSWORD_HINT,
  PASSWORD_MIN_LENGTH,
  passwordRules,
} from "./passwordPolicy";
import {
  App,
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tabs,
} from "antd";
import { PlusOutlined, UploadOutlined } from "@ant-design/icons";
import {
  api,
  type Account,
  type Claim,
  type Person,
  type Group,
  type Settings,
  type Audit,
} from "./api";
import { Pool } from "./Accounts";
import { MailToolField } from "./MailToolField";
import { Dialog, Events, fmt, iso, useResource } from "./ui";
const required = [{ required: true, message: "请填写此项" }];
export function AdminAccounts({
  epoch,
  refresh,
  user,
}: {
  epoch: number;
  refresh: () => void;
  user: Person;
}) {
  const { data: groups = [] } = useResource<Group[]>("/groups", epoch),
    { data: users = [] } = useResource<Person[]>("/users", epoch);
  const [importOpen, setImportOpen] = useState(false);
  return (
    <>
      <Pool
        epoch={epoch}
        refresh={refresh}
        user={user}
        adminMode
        groups={groups}
        users={users}
        toolbar={
          <Button
            type="primary"
            icon={<UploadOutlined aria-hidden="true" />}
            aria-label="批量登记账号"
            onClick={() => setImportOpen(true)}
          >
            <span>
              批量登记<span className="desktop-only-text">账号</span>
            </span>
          </Button>
        }
      />
      {importOpen && (
        <ImportDialog
          groups={groups}
          users={users}
          onClose={() => setImportOpen(false)}
          refresh={refresh}
        />
      )}
    </>
  );
}
function ImportDialog({
  groups,
  users,
  onClose,
  refresh,
}: {
  groups: Group[];
  users: Person[];
  onClose: () => void;
  refresh: () => void;
}) {
  const [preview, setPreview] = useState<{ rows: any[]; errors: any[] }>(),
    [previewText, setPreviewText] = useState(""),
    [form] = Form.useForm();
  const { message } = App.useApp();
  const values = Form.useWatch([], form);
  const [saving, setSaving] = useState(false);
  async function check() {
    try {
      const v = await form.validateFields();
      const data = {
        ...v,
        expires_at: iso(v.expires_at),
        capacity: v.capacity ?? null,
      };
      setPreview(await api("/account-imports/preview", "POST", data));
      setPreviewText(JSON.stringify(v));
    } catch (e) {
      if (e instanceof Error) message.error(e.message);
    }
  }
  return (
    <div className="import-overlay">
      <div className="import-panel">
        <div className="card-top">
          <h2>批量登记账号</h2>
          <Button onClick={onClose}>关闭</Button>
        </div>
        <p className="muted">
          每行一个账号。先检查格式与重复项，全部通过后整批入库。
        </p>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ tier: "5x", group_ids: [], user_ids: [] }}
          onFinish={async (v) => {
            setSaving(true);
            try {
              const result = await api("/account-imports", "POST", {
                ...v,
                expires_at: iso(v.expires_at),
                capacity: v.capacity ?? null,
              });
              message.success(`已登记 ${result.count} 个账号`);
              refresh();
              onClose();
            } catch (e) {
              message.error((e as Error).message);
              setPreview(undefined);
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item name="text" label="账号内容" rules={required}>
            <Input.TextArea
              rows={6}
              autoComplete="off"
              spellCheck={false}
              placeholder="账号邮箱----账号密码----邮箱密码"
              maxLength={500000}
            />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item name="tier" label="本批账号档位" rules={required}>
                <Select
                  options={[
                    { value: "5x", label: "5x" },
                    { value: "20x", label: "20x" },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="capacity" label="人数上限（留空沿用全局）">
                <InputNumber min={1} max={1000} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="quota_reset_interval_days"
            label="额度自动重置间隔（天，留空沿用全局）"
          >
            <InputNumber
              min={1}
              max={365}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <MailToolField useDefault />
          <Form.Item
            rules={[dateTimeRule]}
            name="expires_at"
            label="过期时间（北京时间，留空不过期）"
          >
            <DateTimeInput />
          </Form.Item>
          <Form.Item name="group_ids" label="授权用户组">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={groups.map((g) => ({ value: g.id, label: g.name }))}
            />
          </Form.Item>
          <Form.Item name="user_ids" label="指定用户">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={users.map((u) => ({
                value: u.id,
                label: u.display_name,
              }))}
            />
          </Form.Item>
          <Alert
            type="info"
            message="组和指定用户任一匹配即可见；全部留空则仅管理员可见。"
          />
          {preview && (
            <div className="import-preview">
              <Alert
                type={preview.errors.length ? "error" : "success"}
                message={
                  preview.errors.length
                    ? `${preview.errors.length} 项问题待修正`
                    : `${preview.rows.length} 个账号检查通过`
                }
              />
              {preview.errors.map((e) => (
                <p key={`${e.line}-${e.message}`}>
                  第 {e.line} 行：{e.message}
                </p>
              ))}
              {preview.rows.map((r) => (
                <div key={r.line}>
                  第 {r.line} 行 · {r.email} · {r.tier} ·{" "}
                  {r.mail_tool_name || "不启用自动取码"} · •••• / ••••
                </div>
              ))}
            </div>
          )}
          <div className="dialog-footer">
            <Button onClick={check}>检查并预览</Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={saving}
              disabled={
                !preview ||
                preview.errors.length > 0 ||
                previewText !== JSON.stringify(values)
              }
            >
              确认整批导入
            </Button>
          </div>
        </Form>
      </div>
    </div>
  );
}
export function People({
  epoch,
  refresh,
}: {
  epoch: number;
  refresh: () => void;
}) {
  const { data: users = [], error } = useResource<Person[]>("/users", epoch),
    { data: groups = [] } = useResource<Group[]>("/groups", epoch);
  const [edit, setEdit] = useState<Person | "new">(),
    [groupEdit, setGroupEdit] = useState<Group | "new">();
  const { message, modal } = App.useApp();
  function removeUser(u: Person) {
    modal.confirm({
      title: `删除用户 ${u.display_name}？`,
      content: `将自动结束其 ${u.claims_used} 笔未归还记录并使登录失效。历史保留。`,
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api(`/users/${u.id}`, "DELETE");
          refresh();
        } catch (e) {
          message.error((e as Error).message);
          throw e;
        }
      },
    });
  }
  function removeGroup(g: Group) {
    modal.confirm({
      title: `删除用户组 ${g.name}？`,
      content: "组授权将被撤销，已有领用保留至用户归还。",
      onOk: async () => {
        try {
          await api(`/groups/${g.id}`, "DELETE");
          refresh();
        } catch (e) {
          message.error((e as Error).message);
          throw e;
        }
      },
    });
  }
  return (
    <>
      {error && <Alert type="error" message={error} />}
      <Tabs
        items={[
          {
            key: "users",
            label: `用户 · ${users.length}`,
            children: (
              <>
                <div className="section-action">
                  <Button
                    type="primary"
                    icon={<PlusOutlined aria-hidden="true" />}
                    onClick={() => setEdit("new")}
                  >
                    添加用户
                  </Button>
                </div>
                <Table
                  rowKey="id"
                  dataSource={users}
                  scroll={{ x: 720 }}
                  columns={[
                    {
                      title: "用户",
                      render: (_, u) => (
                        <>
                          <strong>{u.display_name}</strong>
                          <div className="muted">{u.username}</div>
                        </>
                      ),
                    },
                    {
                      title: "角色",
                      render: (_, u) => (
                        <Tag color={u.role === "admin" ? "blue" : "default"}>
                          {u.role === "admin" ? "管理员" : "普通用户"}
                        </Tag>
                      ),
                    },
                    {
                      title: "所属组",
                      render: (_, u) =>
                        u.group_ids.map((id) => (
                          <Tag key={id}>
                            {groups.find((g) => g.id === id)?.name || id}
                          </Tag>
                        )),
                    },
                    {
                      title: "领用名额",
                      render: (_, u) =>
                        `${u.claims_used} / ${u.effective_claim_limit}`,
                    },
                    {
                      title: "操作",
                      render: (_, u) => (
                        <Space>
                          <Button size="small" onClick={() => setEdit(u)}>
                            编辑
                          </Button>
                          <Button
                            size="small"
                            danger
                            onClick={() => removeUser(u)}
                          >
                            删除
                          </Button>
                        </Space>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: "groups",
            label: `用户组 · ${groups.length}`,
            children: (
              <>
                <div className="section-action">
                  <Button type="primary" onClick={() => setGroupEdit("new")}>
                    添加用户组
                  </Button>
                </div>
                <Table
                  rowKey="id"
                  dataSource={groups}
                  columns={[
                    { title: "组名", dataIndex: "name" },
                    {
                      title: "成员数",
                      render: (_, g) => g.user_ids.length,
                    },
                    {
                      title: "操作",
                      render: (_, g) => (
                        <Space>
                          <Button size="small" onClick={() => setGroupEdit(g)}>
                            编辑
                          </Button>
                          <Button
                            size="small"
                            danger
                            onClick={() => removeGroup(g)}
                          >
                            删除
                          </Button>
                        </Space>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
        ]}
      />
      {edit && (
        <Dialog
          title={edit === "new" ? "添加用户" : "编辑用户"}
          initial={edit === "new" ? { role: "user", group_ids: [] } : edit}
          onClose={() => setEdit(undefined)}
          onSubmit={async (v) => {
            const payload = { ...v, claim_limit: v.claim_limit ?? null };
            if (edit !== "new") {
              delete payload.username;
              if (!payload.password) delete payload.password;
            }
            await api(
              edit === "new" ? "/users" : `/users/${edit.id}`,
              edit === "new" ? "POST" : "PATCH",
              payload,
            );
            refresh();
          }}
        >
          <Form.Item name="username" label="登录用户名" rules={required}>
            <Input disabled={edit !== "new"} autoComplete="off" />
          </Form.Item>
          <Form.Item name="display_name" label="显示姓名" rules={required}>
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label={
              edit === "new"
                ? `初始密码（至少 ${PASSWORD_MIN_LENGTH} 位）`
                : "重置密码（留空不修改，修改后需重新登录）"
            }
            rules={passwordRules(edit === "new")}
            extra={PASSWORD_HINT}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="角色">
            <Select
              options={[
                { value: "user", label: "普通用户" },
                { value: "admin", label: "管理员" },
              ]}
            />
          </Form.Item>
          <Form.Item name="group_ids" label="所属用户组">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={groups.map((g) => ({ value: g.id, label: g.name }))}
            />
          </Form.Item>
          <Form.Item name="claim_limit" label="领用数量上限（留空沿用全局）">
            <InputNumber min={1} max={1000} />
          </Form.Item>
        </Dialog>
      )}
      {groupEdit && (
        <Dialog
          title={groupEdit === "new" ? "添加用户组" : "编辑用户组"}
          initial={
            groupEdit === "new"
              ? { user_ids: [] }
              : {
                  ...groupEdit,
                  user_ids: groupEdit.user_ids,
                }
          }
          onClose={() => setGroupEdit(undefined)}
          onSubmit={async (v) => {
            await api(
              groupEdit === "new" ? "/groups" : `/groups/${groupEdit.id}`,
              groupEdit === "new" ? "POST" : "PATCH",
              v,
            );
            refresh();
          }}
        >
          <Form.Item name="name" label="组名" rules={required}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item
            name="user_ids"
            label="组成员"
            extra="选择用户加入此组，移除选中项即可退组；已有领用仍可继续使用和归还。"
          >
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              allowClear
              placeholder="搜索姓名或用户名"
              options={users.map((u) => ({
                value: u.id,
                label: `${u.display_name}（${u.username}）`,
              }))}
            />
          </Form.Item>
        </Dialog>
      )}
    </>
  );
}
export function SystemSettings({
  epoch,
  refresh,
}: {
  epoch: number;
  refresh: () => void;
}) {
  const { data, error } = useResource<Settings>("/settings", epoch);
  const { message } = App.useApp();
  return (
    <Card className="settings-card">
      {error && <Alert type="error" message={error} />}
      <h3>领用与恢复规则</h3>
      <p className="muted">
        单独设置的账号 / 用户上限优先。降低上限不会强制结束已有领用。
      </p>
      {data && (
        <Form
          key={JSON.stringify(data)}
          layout="vertical"
          initialValues={data}
          onFinish={async (v) => {
            try {
              await api("/settings", "PUT", v);
              refresh();
              message.success("设置已保存");
            } catch (e) {
              message.error((e as Error).message);
            }
          }}
        >
          {[
            ["user_claim_limit", "每个用户的默认领用数量", 1000],
            ["account_capacity", "每个账号的默认同时领用人数", 1000],
            ["observation_hours", "异常账号领用观察时长（小时）", 8760],
            ["cooldown_hours", "异常冷却时长（小时）", 8760],
            ["session_days", "登录会话有效期（天，新登录生效）", 90],
            [
              "email_code_timeout_minutes",
              "邮箱验证码等待时长（分钟，新取码生效）",
              30,
            ],
            ["quota_reset_interval_days", "默认额度自动重置间隔（天）", 365],
          ].map(([name, label, max]) => (
            <Form.Item
              key={name}
              name={String(name)}
              label={label}
              rules={required}
            >
              <InputNumber min={1} max={Number(max)} precision={0} />
            </Form.Item>
          ))}
          <Form.Item
            name="quota_depleted_threshold"
            label="额度耗尽阈值（%）"
            rules={required}
            extra="剩余额度严格低于此值时显示为已耗尽；等于阈值或额度未知时不算耗尽。默认 5%，仅影响状态判断，不禁止领用。"
          >
            <InputNumber min={1} max={100} precision={0} />
          </Form.Item>
          <Alert
            type="info"
            message="调整观察或冷却时长会按原有时间起点重新计算；自动恢复显示为“可能恢复”。"
          />
          <Button type="primary" htmlType="submit">
            保存设置
          </Button>
        </Form>
      )}
    </Card>
  );
}
export function Dashboard({
  epoch,
  refresh,
}: {
  epoch: number;
  refresh: () => void;
}) {
  const [q, setQ] = useState(""),
    [state, setState] = useState("all"),
    [page, setPage] = useState(0),
    [tier, setTier] = useState("all"),
    [group, setGroup] = useState("all");
  const { message, modal } = App.useApp();
  const { data: groups = [] } = useResource<Group[]>("/groups", epoch),
    { data: overview = {}, error } = useResource<Record<string, number>>(
      "/overview",
      epoch,
    );
  const path =
    `/claims?scope=all&limit=50&offset=${page * 50}&q=${encodeURIComponent(q)}&state=${state}` +
    (tier === "all" ? "" : `&tier=${tier}`) +
    (group === "all" ? "" : `&group_id=${group}`);
  const { data: filtered = [], error: claimError } = useResource<Claim[]>(
    path,
    epoch,
  );
  const stats = [
    ["账号总数", overview.accounts || 0],
    ["有空位且可领用", overview.available || 0],
    ["已停用账号", overview.disabled || 0],
    ["异常账号", overview.abnormal || 0],
    ["额度耗尽", overview.depleted || 0],
    ["过期账号", overview.expired || 0],
    ["有效领用", overview.active_claims || 0],
    ["待用户归还", overview.pending_claims || 0],
  ];
  function revoke(c: Claim) {
    let reason = "";
    modal.confirm({
      title: "强制回收账号",
      content: (
        <>
          <p>账号人数名额立即释放；用户需主动归还后才释放个人名额。</p>
          <Input.TextArea
            aria-label="回收原因"
            placeholder="填写回收原因"
            onChange={(e) => (reason = e.target.value)}
          />
        </>
      ),
      onOk: async () => {
        try {
          await api(`/claims/${c.id}/revocation`, "PUT", { reason });
          refresh();
        } catch (e) {
          message.error((e as Error).message);
          throw e;
        }
      },
    });
  }
  return (
    <>
      {(error || claimError) && (
        <Alert type="error" message={error || claimError} />
      )}
      <div className="stats-grid">
        {stats.map(([label, value]) => (
          <Card key={label}>
            <span className="muted">{label}</span>
            <strong>{value}</strong>
          </Card>
        ))}
      </div>
      <Card title="全部未归还领用">
        <div className="filter-bar">
          <Input
            placeholder="搜索用户或账号"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(0);
            }}
          />
          <Select
            value={state}
            onChange={(v) => {
              setState(v);
              setPage(0);
            }}
            options={[
              { value: "all", label: "全部领用状态" },
              { value: "active", label: "有效领用" },
              { value: "pending", label: "待用户归还" },
            ]}
          />
          <Select
            value={tier}
            onChange={(v) => {
              setTier(v);
              setPage(0);
            }}
            options={[
              { value: "all", label: "全部档位" },
              { value: "5x", label: "5x" },
              { value: "20x", label: "20x" },
            ]}
          />
          <Select
            value={group}
            onChange={(v) => {
              setGroup(v);
              setPage(0);
            }}
            options={[
              { value: "all", label: "全部用户组" },
              ...groups.map((g) => ({ value: g.id, label: g.name })),
            ]}
          />
        </div>
        <Table
          pagination={false}
          rowKey="id"
          dataSource={filtered}
          scroll={{ x: 650 }}
          columns={[
            { title: "账号", dataIndex: "account_email" },
            { title: "领用人", dataIndex: "user_name" },
            { title: "领用时间", render: (_, c) => fmt(c.claimed_at) },
            {
              title: "状态",
              render: (_, c) => (
                <Tag color={c.invalidated_at ? "warning" : "success"}>
                  {c.invalidated_at ? "已回收，待本人归还" : "有效领用"}
                </Tag>
              ),
            },
            {
              title: "操作",
              render: (_, c) => (
                <Button
                  size="small"
                  disabled={!!c.invalidated_at}
                  onClick={() => revoke(c)}
                >
                  强制回收
                </Button>
              ),
            },
          ]}
        />
        <div className="pagination">
          <Button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            上一页
          </Button>
          <span>第 {page + 1} 页</span>
          <Button
            disabled={filtered.length < 50}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </Card>
    </>
  );
}
export function AuditPage({ epoch }: { epoch: number }) {
  const [before, setBefore] = useState<number | undefined>(),
    [stack, setStack] = useState<(number | undefined)[]>([]),
    [accountId, setAccountId] = useState("all"),
    [userId, setUserId] = useState("all");
  const { data: subjects } = useResource<{
    accounts: { id: string; email: string; deleted: boolean }[];
    users: {
      id: string;
      username: string;
      display_name: string;
      deleted: boolean;
    }[];
  }>("/audit-subjects", epoch);
  const accounts = subjects?.accounts || [],
    users = subjects?.users || [];
  const path =
    "/audit-events?limit=50" +
    (before ? `&before=${before}` : "") +
    (accountId === "all" ? "" : `&account_id=${accountId}`) +
    (userId === "all" ? "" : `&user_id=${userId}`);
  const { data = [], error } = useResource<Audit[]>(path, epoch);
  return (
    <Card className="audit-card">
      {error && <Alert type="error" message={error} />}
      <div className="filter-bar">
        <Select
          allowClear
          aria-label="筛选审计账号"
          placeholder="全部账号"
          showSearch
          optionFilterProp="label"
          value={accountId}
          onChange={(v) => {
            setAccountId(v ?? "all");
            setBefore(undefined);
            setStack([]);
          }}
          options={[
            { value: "all", label: "全部账号" },
            ...accounts.map((a) => ({
              value: a.id,
              label: a.email + (a.deleted ? "（已删除）" : ""),
            })),
          ]}
        />
        <Select
          allowClear
          aria-label="筛选审计用户"
          placeholder="全部用户"
          value={userId}
          onChange={(v) => {
            setUserId(v ?? "all");
            setBefore(undefined);
            setStack([]);
          }}
          options={[
            { value: "all", label: "全部用户" },
            ...users.map((u) => ({
              value: u.id,
              label: `${u.display_name}（${u.username}）${u.deleted ? "（已删除）" : ""}`,
            })),
          ]}
        />
      </div>
      <Events events={data} />
      <div className="pagination">
        <Button
          disabled={!stack.length}
          onClick={() => {
            setBefore(stack.at(-1));
            setStack((s) => s.slice(0, -1));
          }}
        >
          上一页
        </Button>
        <Button
          disabled={data.length < 50}
          onClick={() => {
            setStack((s) => [...s, before]);
            setBefore(data.at(-1)?.id);
          }}
        >
          下一页
        </Button>
      </div>
    </Card>
  );
}
