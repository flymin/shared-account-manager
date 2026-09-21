import { useState } from "react";
import { Button, Checkbox, Popover } from "antd";
import { DownOutlined } from "@ant-design/icons";
import {
  ACCOUNT_STATUSES,
  ALL_ACCOUNT_STATUSES,
  type AccountStatus,
} from "./accountList";

export function AccountStatusFilter({
  value,
  onChange,
}: {
  value: AccountStatus[];
  onChange: (value: AccountStatus[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const all = value.length === ALL_ACCOUNT_STATUSES.length;
  const summary = all
    ? "全部状态"
    : value.length === 0
      ? "未选择状态"
      : value.length === 1
        ? ACCOUNT_STATUSES.find((s) => s.value === value[0])!.label
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
                onChange(event.target.checked ? [...ALL_ACCOUNT_STATUSES] : [])
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
            options={ACCOUNT_STATUSES.map((s) => ({ ...s }))}
          />
          <p className="small muted">
            多选展示所选类别；每个账号按异常、可能恢复、额度已耗尽、人数已满、正常的顺序归入一类。
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
