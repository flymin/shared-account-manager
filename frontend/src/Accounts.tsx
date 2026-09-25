import { AccountNotes } from "./AccountNotes";
import { DateTimeInput, dateTimeRule } from "./DateTimeInput";
import { useEffect, useState, type ReactNode } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  ConfigProvider,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Pagination,
  Radio,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import {
  ArrowRightOutlined,
  SearchOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { api, type Account, type Claim, type Person, type Group } from "./api";
import {
  accountStatus,
  isInAccountHall,
  ALL_ACCOUNT_STATUSES,
  ALL_ADMIN_ACCOUNT_STATUSES,
  compareAccounts,
  type AccountSort,
  type AccountStatus,
} from "./accountList";
import { AccountStatusFilter } from "./AccountStatusFilter";
import { MailToolField } from "./MailToolField";
import { AnomalyField, TierField, useTierFilters } from "./AccountOptions";
import {
  Credentials,
  CopyButton,
  Dialog,
  EventHistory,
  Health,
  Quota,
  fmt,
  localDate,
  iso,
  useResource,
} from "./ui";
function Expiry({ value }: { value?: string | null }) {
  if (!value) return <span className="account-expiry muted">不过期</span>;
  const expired = new Date(value).getTime() <= Date.now();
  return (
    <time
      dateTime={value}
      className={`account-expiry${expired ? " is-expired" : ""}`}
      title={`北京时间${expired ? " · 已到期" : ""}`}
    >
      {fmt(value)}
    </time>
  );
}
export type Action = {
  mode: "return" | "quota" | "report" | "edit";
  a: Account;
  claim?: Claim;
};
const required = [{ required: true, message: "请填写此项" }];
export function AccountDialog({
  action,
  onClose,
  refresh,
  groups = [],
  users = [],
}: {
  action: Action;
  onClose: () => void;
  refresh: () => void;
  groups?: Group[];
  users?: Person[];
}) {
  const { a, mode, claim } = action;
  const [kind, setKind] = useState("normal");
  const initial =
    mode === "edit"
      ? { ...a, expires_at: localDate(a.expires_at) }
      : {
          quota: mode === "return" ? 0 : a.quota,
          reset_at: localDate(a.reset_at),
          kind: "normal",
          health_action: "maintain",
          categories: [],
        };
  const title = {
    return: "归还账号",
    quota: "更新剩余额度",
    report: "反馈账号异常",
    edit: "编辑账号",
  }[mode];
  async function submit(v: any) {
    if (mode === "edit") {
      const payload = {
        ...v,
        expires_at: iso(v.expires_at),
        capacity: v.capacity ?? null,
        quota_reset_interval_days: v.quota_reset_interval_days ?? null,
      };
      if (!payload.password) delete payload.password;
      if (!payload.auth_password) delete payload.auth_password;
      await api(`/accounts/${a.id}`, "PATCH", payload);
    }
    if (mode === "quota")
      await api(`/accounts/${a.id}/quota-reports`, "POST", {
        quota: v.quota,
        reset_at: iso(v.reset_at),
      });
    if (mode === "report")
      await api(`/accounts/${a.id}/health-reports`, "POST", {
        action: "report",
        version: a.health_version,
        categories: v.categories || [],
        note: v.note || "",
      });
    if (mode === "return")
      await api(`/claims/${claim!.id}/return`, "PUT", {
        kind: v.kind,
        quota: v.kind === "normal" ? v.quota : null,
        reset_at: v.kind === "normal" ? iso(v.reset_at) : null,
        categories: v.kind === "abnormal" ? v.categories || [] : [],
        note: v.kind === "abnormal" ? v.note || "" : "",
        health_action: v.health_action || null,
        health_version: a.health_version,
      });
    refresh();
  }
  return (
    <Dialog
      title={title}
      initial={initial}
      onClose={onClose}
      onSubmit={submit}
      okText={mode === "return" ? "确认归还" : "保存"}
    >
      <p className="modal-account">
        {a.email} <Tag>{a.tier_name || a.tier}</Tag>
      </p>
      {mode === "return" && (
        <Form.Item name="kind">
          <Radio.Group
            onChange={(e) => setKind(e.target.value)}
            optionType="button"
            options={[
              { value: "normal", label: "正常归还" },
              { value: "abnormal", label: "异常归还" },
            ]}
          />
        </Form.Item>
      )}
      {(mode === "quota" || (mode === "return" && kind === "normal")) && (
        <>
          <Form.Item name="quota" label="剩余额度（%）" rules={required}>
            <InputNumber
              min={0}
              max={100}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            name="reset_at"
            label="下次重置时间（北京时间）"
            rules={[...(mode === "return" ? required : []), dateTimeRule]}
          >
            <DateTimeInput />
          </Form.Item>
        </>
      )}
      {(mode === "report" || (mode === "return" && kind === "abnormal")) && (
        <>
          <Alert
            type="info"
            showIcon
            message="选择异常类别或填写问题，至少填写一项。"
          />
          <AnomalyField />
          <Form.Item name="note" label="异常描述">
            <Input.TextArea
              rows={3}
              maxLength={2000}
              showCount
              placeholder="补充异常现象，便于其他用户判断"
            />
          </Form.Item>
        </>
      )}
      {mode === "return" && kind === "normal" && a.health !== "normal" && (
        <Form.Item name="health_action" label="账号已有异常标记">
          <Radio.Group
            options={[
              { value: "maintain", label: "维持异常标记" },
              { value: "clear", label: "已测试正常，清除标记" },
            ]}
          />
        </Form.Item>
      )}
      {mode === "edit" && (
        <>
          <TierField current={a.tier} />
          <Form.Item name="capacity" label="同时领用人数（留空沿用全局）">
            <InputNumber min={1} max={1000} />
          </Form.Item>
          <Form.Item
            name="quota_reset_interval_days"
            label="额度自动重置间隔（天，留空沿用全局）"
            extra="范围 1–365 天。更改间隔不改变已登记的下次重置时间。"
          >
            <InputNumber
              min={1}
              max={365}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            rules={[dateTimeRule]}
            name="expires_at"
            label="过期时间（北京时间，留空不过期）"
          >
            <DateTimeInput />
          </Form.Item>
          <MailToolField />
          <Form.Item name="group_ids" label="可见用户组">
            <Select
              mode="multiple"
              showSearch
              optionFilterProp="label"
              options={groups.map((g) => ({ value: g.id, label: g.name }))}
            />
          </Form.Item>
          <Form.Item name="user_ids" label="指定可见用户">
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
          <Form.Item name="password" label="新账号密码（留空不修改）">
            <Input.Password autoComplete="new-password" maxLength={256} />
          </Form.Item>
          <Form.Item name="auth_password" label="新邮箱密码（留空不修改）">
            <Input.Password autoComplete="new-password" maxLength={256} />
          </Form.Item>
        </>
      )}
    </Dialog>
  );
}
export function Pool({
  epoch,
  refresh,
  user,
  adminMode = false,
  groups = [],
  users = [],
  toolbar,
}: {
  epoch: number;
  refresh: () => void;
  user: Person;
  adminMode?: boolean;
  groups?: Group[];
  users?: Person[];
  toolbar?: ReactNode;
}) {
  const { data = [], error } = useResource<Account[]>(
    adminMode ? "/accounts" : "/accounts?scope=hall",
    epoch,
  );
  const tierFilters = useTierFilters();
  const { message, modal } = App.useApp();
  const [q, setQ] = useState(""),
    [tier, setTier] = useState("all"),
    [statuses, setStatuses] = useState<AccountStatus[]>(() => [
      ...(adminMode ? ALL_ADMIN_ACCOUNT_STATUSES : ALL_ACCOUNT_STATUSES),
    ]),
    [group, setGroup] = useState("all");
  const [sort, setSort] = useState<AccountSort | undefined>(() =>
    adminMode ? undefined : { key: "quota", order: "descend" },
  );
  const effectiveSort: AccountSort = sort ?? {
    key: "email",
    order: "ascend",
  };
  const [pageSize, setPageSize] = useState(20);
  const [currentPage, setCurrentPage] = useState(1);
  const [detail, setDetail] = useState<Account>(),
    [action, setAction] = useState<Action>();
  const groupNames = new Map(groups.map((g) => [g.id, g.name]));
  const userNames = new Map(
    users.map((u) => [
      u.id,
      u.display_name === u.username
        ? u.username
        : `${u.display_name}（${u.username}）`,
    ]),
  );
  function accessList(a: Account, kind: "groups" | "users") {
    const ids = (kind === "groups" ? a.group_ids : a.user_ids) || [];
    const names = kind === "groups" ? groupNames : userNames;
    if (!ids.length)
      return (
        <span
          className="muted"
          title={
            !a.group_ids?.length && !a.user_ids?.length
              ? "仅管理员可见"
              : undefined
          }
        >
          未指定
        </span>
      );
    return (
      <span className="account-access-list">
        {ids.map((id, index) => (
          <span key={id}>
            {index > 0 && "、"}
            {names.get(id) || "信息未加载"}
          </span>
        ))}
      </span>
    );
  }
  const filtered = data
    .filter(
      (a) =>
        (adminMode || isInAccountHall(a)) &&
        a.email.toLowerCase().includes(q.toLowerCase()) &&
        (tier === "all" || a.tier === tier) &&
        (group === "all" || a.group_ids?.includes(group)) &&
        statuses.includes(accountStatus(a)),
    )
    .sort((a, b) => compareAccounts(a, b, effectiveSort));
  const pageCount = Math.ceil(filtered.length / pageSize);
  const page = Math.min(currentPage, Math.max(1, pageCount));
  const pageAccounts = filtered.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => {
    setCurrentPage((value) => Math.min(value, Math.max(1, pageCount)));
  }, [pageCount]);
  async function claim(a: Account, ack = false) {
    try {
      await api("/claims", "POST", {
        account_id: a.id,
        acknowledge_warning: ack,
      });
      refresh();
      message.success("领用成功，请到“我的领用”查看两段密码");
    } catch (e) {
      message.error((e as Error).message);
      refresh();
    }
  }
  function beginClaim(a: Account) {
    if (a.health === "abnormal")
      modal.confirm({
        title: "此账号存在异常，仍要领用吗？",
        content: (
          <>
            <p>{(a.health_category_names || a.health_categories).join("、")}</p>
            <p>{a.health_note}</p>
            <p>领用后可登录测试并主动确认恢复。</p>
          </>
        ),
        okText: "确认并领用",
        onOk: () => claim(a, true),
      });
    else void claim(a);
  }
  function clear(a: Account) {
    modal.confirm({
      title: "确认账号已恢复正常？",
      content: "请在测试可用后清除标记，系统会保留原异常历史。",
      onOk: async () => {
        try {
          await api(`/accounts/${a.id}/health-reports`, "POST", {
            action: "clear",
            version: a.health_version,
          });
          refresh();
          message.success("已确认恢复");
        } catch (e) {
          message.error((e as Error).message);
          throw e;
        }
      },
    });
  }
  function remove(a: Account) {
    let reason = "";
    modal.confirm({
      title: "删除账号",
      content: (
        <>
          <p>
            将回收 {a.active_count}{" "}
            笔有效领用。原用户需主动归还后才释放个人名额。
          </p>
          <Input.TextArea
            aria-label="删除原因"
            placeholder="请输入删除原因"
            onChange={(e) => (reason = e.target.value)}
          />
        </>
      ),
      okText: "确认删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api(`/accounts/${a.id}`, "DELETE", { reason });
          refresh();
        } catch (e) {
          message.error((e as Error).message);
          throw e;
        }
      },
    });
  }
  function setActivation(a: Account) {
    modal.confirm({
      title: a.disabled ? "恢复启用账号" : "停用账号",
      content: (
        <>
          <p className="break-word">{a.email}</p>
          <p>
            {a.disabled
              ? "恢复后，未过期且有空位的账号可再次领用，仍需符合个人名额和访问权限。"
              : "停用后禁止新的领用，已有领用仍可使用、反馈和归还。账号资料与历史保留，可随时恢复。"}
          </p>
        </>
      ),
      okText: a.disabled ? "确认恢复启用" : "确认停用",
      onOk: async () => {
        try {
          await api(`/accounts/${a.id}/activation`, "PUT", {
            enabled: a.disabled,
          });
          message.success(a.disabled ? "账号已恢复启用" : "账号已停用");
          refresh();
        } catch (error) {
          message.error((error as Error).message);
          throw error;
        }
      },
    });
  }
  const actions = (a: Account) => (
    <Space
      className={`account-actions${adminMode ? " account-actions-admin" : ""}`}
      wrap
      size={4}
    >
      {adminMode ? (
        <>
          <Button size="small" onClick={() => setDetail(a)}>
            详情
          </Button>
          <Button size="small" onClick={() => setAction({ mode: "edit", a })}>
            编辑
          </Button>
          <Button size="small" onClick={() => setAction({ mode: "quota", a })}>
            额度
          </Button>
          <Button size="small" onClick={() => setAction({ mode: "report", a })}>
            异常
          </Button>
          {a.health !== "normal" && (
            <Button size="small" onClick={() => clear(a)}>
              确认恢复
            </Button>
          )}
          <Button
            size="small"
            className={a.disabled ? undefined : "account-disable"}
            onClick={() => setActivation(a)}
          >
            {a.disabled ? "恢复启用" : "停用"}
          </Button>
          <Button size="small" danger onClick={() => remove(a)}>
            删除
          </Button>
        </>
      ) : (
        <>
          <div className="claim-control">
            <Button
              type="primary"
              disabled={!a.can_claim}
              onClick={() => beginClaim(a)}
              icon={<ArrowRightOutlined aria-hidden="true" />}
            >
              {a.has_open_claim ? "已领用" : "领用"}
            </Button>
            {!a.can_claim &&
              a.blocked_reasons.some(
                (reason) => reason === "个人名额已满" || reason === "人数已满",
              ) && (
                <small className="claim-blocked-reason">
                  {a.blocked_reasons.includes("个人名额已满")
                    ? "个人名额已满"
                    : "人数已满"}
                </small>
              )}
          </div>
          <Button type="text" onClick={() => setDetail(a)}>
            记录
          </Button>
        </>
      )}
    </Space>
  );
  const currentDetail = detail
    ? data.find((a) => a.id === detail.id)
    : undefined;
  return (
    <>
      {error && <Alert type="error" message={error} showIcon />}
      <ConfigProvider theme={{ token: { controlHeight: 38 } }}>
        <div
          className={`filter-bar account-filters${adminMode ? " account-filters-admin" : ""}`}
        >
          <Input
            prefix={<SearchOutlined aria-hidden="true" />}
            placeholder="搜索账号邮箱"
            aria-label="搜索账号邮箱"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setCurrentPage(1);
            }}
            allowClear
          />
          <Select
            aria-label="筛选类型"
            value={tier}
            onChange={(value) => {
              setTier(value);
              setCurrentPage(1);
            }}
            options={tierFilters}
          />
          <AccountStatusFilter
            value={statuses}
            includeDisabled={adminMode}
            onChange={(value) => {
              setStatuses(value);
              setCurrentPage(1);
            }}
          />
          {adminMode && (
            <Select
              className="account-group-filter"
              aria-label="筛选用户组"
              value={group}
              onChange={(value) => {
                setGroup(value);
                setCurrentPage(1);
              }}
              options={[
                { value: "all", label: "全部用户组" },
                ...groups.map((g) => ({ value: g.id, label: g.name })),
              ]}
            />
          )}
          <div className="filter-actions">
            {toolbar}
            <Button
              icon={<ReloadOutlined aria-hidden="true" />}
              onClick={refresh}
              aria-label="刷新账号"
            />
          </div>
        </div>
      </ConfigProvider>
      <div className="list-caption">
        <div className="list-count-controls">
          <span>
            {adminMode ? "账号资产" : "可见账号"}{" "}
            <strong>{filtered.length}</strong>
          </span>
          <span className="page-summary">
            每页 {pageSize} 个 · 共 {pageCount} 页
          </span>
          <Select
            size="small"
            aria-label="每页账号数量"
            value={pageSize}
            onChange={(value) => {
              setPageSize(value);
              setCurrentPage(1);
            }}
            options={[10, 20, 50, 100].map((value) => ({
              value,
              label: `${value} 个 / 页`,
            }))}
          />
        </div>
        <span>每 15 秒同步 · 时间为北京时间</span>
      </div>
      <div className="mobile-account-sort">
        <span className="muted">排序</span>
        <Select
          aria-label="账号排序"
          value={`${effectiveSort.key}:${effectiveSort.order}`}
          onChange={(value) => {
            const [key, order] = value.split(":");
            setSort({ key, order } as AccountSort);
            setCurrentPage(1);
          }}
          options={[
            { value: "email:ascend", label: "账号名 ↑ 升序" },
            { value: "email:descend", label: "账号名 ↓ 降序" },
            { value: "quota:descend", label: "剩余额度 ↓ 降序" },
            { value: "quota:ascend", label: "剩余额度 ↑ 升序" },
            { value: "reset_at:ascend", label: "下次重置 ↑ 近到远" },
            { value: "reset_at:descend", label: "下次重置 ↓ 远到近" },
          ]}
        />
      </div>
      <div className="desktop-table">
        <Table
          rowKey="id"
          dataSource={pageAccounts}
          onChange={(_, __, sorter) => {
            const selected = Array.isArray(sorter) ? sorter[0] : sorter;
            if (
              (selected.columnKey === "email" ||
                selected.columnKey === "quota" ||
                selected.columnKey === "reset_at") &&
              selected.order
            ) {
              setSort({
                key: selected.columnKey,
                order: selected.order,
              });
              setCurrentPage(1);
            }
          }}
          pagination={false}
          scroll={{ x: adminMode ? 1440 : 1080 }}
          columns={[
            {
              title: "账号",
              key: "email",
              sorter: true,
              sortDirections: ["ascend", "descend", "ascend"],
              sortOrder: sort?.key === "email" ? sort.order : null,
              width: 304,
              render: (_, a) => (
                <>
                  <div className="account-email">
                    {a.email}
                    <CopyButton value={a.email} label="账号邮箱" />
                  </div>
                  <div className="account-meta">
                    <Tag className="tier-tag">{a.tier_name || a.tier}</Tag>
                    <small className="muted">启用 {fmt(a.created_at)}</small>
                  </div>
                  {adminMode && (
                    <small className="muted">
                      邮箱：{a.mail_tool_name || "不启用自动取码"}
                    </small>
                  )}
                </>
              ),
            },
            ...(adminMode
              ? [
                  {
                    title: "可见用户组",
                    key: "groups",
                    width: 130,
                    render: (_: unknown, a: Account) => accessList(a, "groups"),
                  },
                  {
                    title: "指定可用用户",
                    key: "users",
                    width: 170,
                    render: (_: unknown, a: Account) => accessList(a, "users"),
                  },
                ]
              : []),
            {
              title: "剩余额度",
              key: "quota",
              sorter: true,
              sortDirections: ["descend", "ascend", "descend"],
              sortOrder: sort?.key === "quota" ? sort.order : null,
              width: 175,
              render: (_, a) => <Quota a={a} showReset={false} />,
            },
            {
              title: "下次重置",
              key: "reset_at",
              sorter: true,
              sortDirections: ["ascend", "descend", "ascend"],
              sortOrder: sort?.key === "reset_at" ? sort.order : null,
              width: 160,
              render: (_, a) =>
                a.reset_at ? (
                  fmt(a.reset_at)
                ) : (
                  <span className="muted">未设置</span>
                ),
            },
            {
              title: "账号状态",
              key: "health",
              width: 130,
              render: (_, a) => (
                <>
                  <span className="account-status-with-notes">
                    <Health a={a} />
                    <AccountNotes
                      accountId={a.id}
                      count={a.note_count}
                      epoch={epoch}
                      refresh={refresh}
                      canAdd={adminMode}
                    />
                  </span>
                  <div className="muted small">
                    {(a.health_category_names || a.health_categories).join(
                      " · ",
                    )}
                  </div>
                </>
              ),
            },
            {
              title: "当前领用",
              key: "occupancy",
              width: 105,
              render: (_, a) => (
                <>
                  <strong>
                    {a.active_count} / {a.effective_capacity}
                  </strong>
                  <div className="muted small">
                    {a.current_users.join("、") || "暂无领用人"}
                  </div>
                </>
              ),
            },
            {
              title: "最近额度更新",
              key: "updated",
              width: 165,
              render: (_, a) => (
                <>
                  {a.recent_updates[0] ? (
                    <>
                      <div className="small">
                        {a.recent_updates[0].actor} · 剩余{" "}
                        {a.recent_updates[0].details.quota}%
                      </div>
                      <small className="muted">
                        {fmt(a.recent_updates[0].created_at)}
                      </small>
                    </>
                  ) : (
                    <span className="muted">待首次反馈</span>
                  )}
                </>
              ),
            },
            {
              title: "到期日",
              key: "expires_at",
              width: 145,
              render: (_, a) => <Expiry value={a.expires_at} />,
            },
            {
              title: "操作",
              key: "action",
              fixed: adminMode ? "right" : undefined,
              width: adminMode ? 200 : 140,
              render: (_, a) => actions(a),
            },
          ]}
        />
      </div>
      <div className="mobile-cards">
        {pageAccounts.map((a) => (
          <Card key={a.id} className="account-card">
            <div className="card-top">
              <strong>{a.email}</strong>
              <Tag className="tier-tag">{a.tier_name || a.tier}</Tag>
            </div>
            <span className="account-status-with-notes">
              <Health a={a} />
              <AccountNotes
                accountId={a.id}
                count={a.note_count}
                epoch={epoch}
                refresh={refresh}
                canAdd={adminMode}
              />
            </span>
            {adminMode && (
              <div className="account-access-meta">
                <div>
                  <span className="muted">邮箱取码工具：</span>
                  {a.mail_tool_name || "不启用自动取码"}
                </div>
                <div>
                  <span className="muted">可见用户组：</span>
                  {accessList(a, "groups")}
                </div>
                <div>
                  <span className="muted">指定可用用户：</span>
                  {accessList(a, "users")}
                </div>
              </div>
            )}
            <div className="card-expiry">
              到期日：
              <Expiry value={a.expires_at} />
            </div>
            <Quota a={a} showReset={false} />
            <div className="card-reset">
              <span className="muted">下次重置：</span>
              {a.reset_at ? (
                fmt(a.reset_at)
              ) : (
                <span className="muted">未设置</span>
              )}
            </div>
            <div className="card-meta">
              <span>
                领用 {a.active_count} / {a.effective_capacity}
              </span>
              <span>{a.current_users.join("、") || "暂无领用人"}</span>
            </div>
            {actions(a)}
          </Card>
        ))}
        {!filtered.length && <Empty description="暂无符合条件的账号" />}
      </div>
      <Pagination
        className="account-pagination"
        current={page}
        pageSize={pageSize}
        total={filtered.length}
        onChange={setCurrentPage}
        showSizeChanger={false}
        hideOnSinglePage
      />
      <Drawer
        open={!!detail}
        title="账号详情与记录"
        onClose={() => setDetail(undefined)}
        width={540}
        destroyOnHidden
      >
        {currentDetail ? (
          <>
            <h3 className="break-word">{currentDetail.email}</h3>
            <span className="account-status-with-notes">
              <Health a={currentDetail} />
              <AccountNotes
                accountId={currentDetail.id}
                count={currentDetail.note_count}
                epoch={epoch}
                refresh={refresh}
                canAdd={adminMode}
              />
            </span>
            <p className="muted">
              启用 {fmt(currentDetail.created_at)} ·{" "}
              {currentDetail.expires_at
                ? `过期 ${fmt(currentDetail.expires_at)}`
                : "不过期"}
            </p>
            {currentDetail.health_note && (
              <Alert type="warning" message={currentDetail.health_note} />
            )}
            <Quota a={currentDetail} />
            {adminMode && (
              <p className="small muted">
                邮箱取码工具：{currentDetail.mail_tool_name || "不启用自动取码"}
              </p>
            )}
            {user.role === "admin" && (
              <Credentials
                accountId={currentDetail.id}
                epoch={epoch}
                manageTwoFactor={adminMode && user.role === "admin"}
              />
            )}
            {adminMode && (
              <div className="account-notes-entry">
                <AccountNotes
                  accountId={currentDetail.id}
                  count={currentDetail.note_count}
                  epoch={epoch}
                  refresh={refresh}
                  canAdd
                  entry
                />
              </div>
            )}
            <h3>更新记录</h3>
            <EventHistory accountId={currentDetail.id} epoch={epoch} />
          </>
        ) : (
          <Empty description="账号已删除或当前不可见" />
        )}
      </Drawer>
      {action && (
        <AccountDialog
          action={action}
          onClose={() => setAction(undefined)}
          refresh={refresh}
          groups={groups}
          users={users}
        />
      )}
    </>
  );
}
export function MyClaims({
  epoch,
  refresh,
  history = false,
}: {
  epoch: number;
  refresh: () => void;
  history?: boolean;
}) {
  const [page, setPage] = useState(0);
  const { data = [], error } = useResource<Claim[]>(
    `/claims?history=${history}&offset=${page * 50}&limit=50`,
    epoch,
  );
  const [action, setAction] = useState<Action>();
  const { message, modal } = App.useApp();
  useEffect(() => {
    if (
      action &&
      !data.some(
        (c) =>
          c.account?.id === action.a.id && !c.invalidated_at && !c.returned_at,
      )
    )
      setAction(undefined);
  }, [data, action]);
  async function acknowledge(c: Claim) {
    try {
      await api(`/claims/${c.id}/return`, "PUT", { kind: "acknowledge" });
      refresh();
      message.success("已归还，个人名额已释放");
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  async function clear(a: Account) {
    try {
      await api(`/accounts/${a.id}/health-reports`, "POST", {
        action: "clear",
        version: a.health_version,
      });
      refresh();
      message.success("已确认恢复");
    } catch (e) {
      message.error((e as Error).message);
    }
  }
  return (
    <>
      {error && <Alert type="error" message={error} />}
      <div className="claim-grid">
        {data.map((c) => (
          <Card key={c.id} className="claim-card">
            <div className="card-top">
              <strong className="break-word">
                {c.account?.email ||
                  (c.invalidation_kind === "deleted"
                    ? "账号已删除"
                    : "账号已回收")}
              </strong>
              {c.account && (
                <Tag className="tier-tag">
                  {c.account.tier_name || c.account.tier}
                </Tag>
              )}
            </div>
            <p className="small muted">
              领用 {fmt(c.claimed_at)}
              {history && ` · 归还 ${fmt(c.returned_at)}`}
            </p>
            {history ? (
              <Tag>
                {c.return_kind === "abnormal"
                  ? "异常归还"
                  : c.return_kind === "normal"
                    ? "正常归还"
                    : c.return_kind === "user_deleted"
                      ? "删除用户自动归还"
                      : "确认归还"}
              </Tag>
            ) : c.account ? (
              <>
                <span className="account-status-with-notes">
                  <Health a={c.account} />
                  <AccountNotes
                    accountId={c.account.id}
                    count={c.account.note_count}
                    epoch={epoch}
                    refresh={refresh}
                  />
                </span>
                {c.account.health_note && (
                  <p className="anomaly-note">
                    {(
                      c.account.health_category_names ||
                      c.account.health_categories
                    ).join("、")}{" "}
                    {c.account.health_note}
                  </p>
                )}
                <Quota a={c.account} />
                <Credentials accountId={c.account.id} epoch={epoch} />
                <div className="claim-actions">
                  <AccountNotes
                    accountId={c.account.id}
                    count={c.account.note_count}
                    epoch={epoch}
                    refresh={refresh}
                    canAdd
                    entry
                  />
                  <Button
                    type="primary"
                    onClick={() =>
                      setAction({ mode: "return", a: c.account!, claim: c })
                    }
                  >
                    归还账号
                  </Button>
                  <Button
                    onClick={() => setAction({ mode: "quota", a: c.account! })}
                  >
                    更新额度
                  </Button>
                  <Button
                    onClick={() => setAction({ mode: "report", a: c.account! })}
                  >
                    反馈异常
                  </Button>
                  {c.account.health !== "normal" && (
                    <Button
                      onClick={() =>
                        modal.confirm({
                          title: "已登录测试，确认恢复正常？",
                          onOk: () => clear(c.account!),
                        })
                      }
                    >
                      确认恢复
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <>
                <Alert
                  type="warning"
                  showIcon
                  message="账号访问已撤回"
                  description="此记录仍占用个人领用名额，请主动归还后再领用新账号。"
                />
                <p className="muted">{c.invalidation_reason}</p>
                <Button type="primary" onClick={() => acknowledge(c)}>
                  归还并释放名额
                </Button>
              </>
            )}
          </Card>
        ))}
      </div>
      {!data.length && (
        <Empty description={history ? "暂无归还记录" : "暂无领用中的账号"} />
      )}
      <div className="pagination">
        <Button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
          上一页
        </Button>
        <span>第 {page + 1} 页</span>
        <Button
          disabled={data.length < 50}
          onClick={() => setPage((p) => p + 1)}
        >
          下一页
        </Button>
      </div>
      {action && (
        <AccountDialog
          action={action}
          onClose={() => setAction(undefined)}
          refresh={refresh}
        />
      )}
    </>
  );
}
