import { useState } from "react";
import { Button, Checkbox, Popover } from "antd";
import { DownOutlined } from "@ant-design/icons";
import {
  ACCOUNT_STATUSES,
  ALL_ACCOUNT_STATUSES,
  ALL_ADMIN_ACCOUNT_STATUSES,
  ADMIN_ACCOUNT_STATUSES,
  type AccountStatus,
} from "./accountList";

export function AccountStatusFilter({
  value,
  onChange,
  includeDisabled = false,
}: {
  value: AccountStatus[];
  onChange: (value: AccountStatus[]) => void;
  includeDisabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const options = includeDisabled ? ADMIN_ACCOUNT_STATUSES : ACCOUNT_STATUSES;
  const allStatuses = includeDisabled
    ? ALL_ADMIN_ACCOUNT_STATUSES
    : ALL_ACCOUNT_STATUSES;
  const all =
    value.length === allStatuses.length &&
    allStatuses.every((status) => value.includes(status));
  const summary = all
    ? "全部状态"
    : value.length === 0
      ? "未选择状态"
      : value.length === 1
        ? options.find((s) => s.value === value[0])!.label
        : `已选 ${value.length} 种状态`;
  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      open={open}
      onOpenChange={setOpen}
      content={
        <div className="status-filter-panel">
          <div className="status-filter-heading">
            <Checkbox
              checked={all}
              indeterminate={value.length > 0 && !all}
              onChange={(event) =>
                onChange(event.target.checked ? [...allStatuses] : [])
              }
            >
              全选
            </Checkbox>
            <Button type="link" size="small" onClick={() => onChange([])}>
              清空
            </Button>
          </div>
          <Checkbox.Group
            value={value}
            onChange={(selected) => onChange(selected as AccountStatus[])}
            options={options.map((s) => ({ ...s }))}
          />
          <p className="small muted">
            {includeDisabled
              ? "多选显示任一条件匹配的账号，结果不重复。“已过期”按到期时间判断，包含同时停用的账号；其他状态保持原有分类。"
              : "多选展示所选类别；账号按异常、可能恢复、额度已耗尽、人数已满、正常归类。"}
          </p>
        </div>
      }
    >
      <Button
        className="status-filter-trigger"
        aria-label="筛选状态"
        aria-expanded={open}
      >
        {summary}
        <DownOutlined />
      </Button>
    </Popover>
  );
}
