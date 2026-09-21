import { createContext, useContext, useEffect, type ReactNode } from "react";
import { Alert, Button, Form, Input, InputNumber, Select, Switch } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { AccountOptions } from "./api";
import { useResource } from "./ui";

type Catalog = AccountOptions & { cooldown_hours: number };
const OptionsContext = createContext<{ data?: Catalog; error: string }>({
  error: "",
});

export function AccountOptionsProvider({
  epoch,
  children,
}: {
  epoch: number;
  children: ReactNode;
}) {
  const { data, error } = useResource<Catalog>("/account-options", epoch);
  return (
    <OptionsContext.Provider value={{ data, error }}>
      {error && <Alert type="warning" message={`账号选项加载失败：${error}`} />}
      {children}
    </OptionsContext.Provider>
  );
}

export const useAccountOptions = () => useContext(OptionsContext);

export function useTierFilters() {
  const { data } = useAccountOptions();
  return [
    { value: "all", label: "全部档位" },
    ...(data?.tiers || []).map((item) => ({
      value: item.id,
      label: item.name + (item.enabled ? "" : "（已停用）"),
    })),
  ];
}

export function TierField({
  label = "账号档位",
  current,
  useDefault = false,
}: {
  label?: string;
  current?: string;
  useDefault?: boolean;
}) {
  const form = Form.useFormInstance();
  const { data, error } = useAccountOptions();
  useEffect(() => {
    if (
      data &&
      useDefault &&
      form.getFieldValue("tier") === undefined &&
      !form.isFieldTouched("tier")
    )
      form.setFieldValue("tier", data.tiers.find((item) => item.enabled)?.id);
  }, [data, form, useDefault]);
  return (
    <Form.Item
      name="tier"
      label={label}
      extra={error}
      rules={[
        { required: true, message: "请选择账号档位" },
        {
          validator: async (_, value) => {
            if (!data) throw new Error("账号档位尚未加载，请稍后重试");
            if (
              !data.tiers.some(
                (item) =>
                  item.id === value && (item.enabled || item.id === current),
              )
            )
              throw new Error("请选择当前启用的账号档位");
          },
        },
      ]}
    >
      <Select
        loading={!data && !error}
        disabled={!data}
        options={(data?.tiers || [])
          .filter((item) => item.enabled || item.id === current)
          .map((item) => ({
            value: item.id,
            label: item.name + (item.enabled ? "" : "（已停用）"),
            disabled: !item.enabled,
          }))}
      />
    </Form.Item>
  );
}

export function AnomalyField() {
  const { data, error } = useAccountOptions();
  return (
    <Form.Item
      name="categories"
      label="异常类别"
      extra={error || "可多选；未选择类别时，请填写异常描述。"}
      rules={[
        {
          validator: async (_, values: string[] | undefined) => {
            if (
              values?.some(
                (value) =>
                  !data?.anomaly_categories.some(
                    (item) => item.id === value && item.enabled,
                  ),
              )
            )
              throw new Error("类别已变更，请重新选择");
          },
        },
      ]}
    >
      <Select
        mode="multiple"
        loading={!data && !error}
        disabled={!data}
        placeholder="选择类别"
        options={(data?.anomaly_categories || [])
          .filter((item) => item.enabled)
          .map((item) => ({ value: item.id, label: item.name }))}
      />
    </Form.Item>
  );
}

export function AccountOptionsEditor() {
  return (
    <>
      <OptionsList kind="tiers" title="账号档位" />
      <OptionsList kind="anomaly_categories" title="异常类别" />
    </>
  );
}

function OptionsList({
  kind,
  title,
}: {
  kind: keyof AccountOptions;
  title: string;
}) {
  const form = Form.useFormInstance();
  const anomaly = kind === "anomaly_categories";
  const defaultHours = Form.useWatch("cooldown_hours", form);
  return (
    <section className="options-editor" aria-label={`${title}配置`}>
      <h3>{title}</h3>
      <p className="muted">
        {anomaly
          ? `恢复间隔留空沿用全局 ${defaultHours ?? "—"} 小时；多选取最长间隔，只有自由文本时沿用全局值。`
          : "至少保留一个启用档位。改名会同步更新账号显示，停用后不能用于新登记或转入。"}
      </p>
      <Form.List
        name={["account_options", kind]}
        rules={[
          {
            validator: async (_, items = []) => {
              if (items.length > 50) throw new Error("最多可配置 50 个选项");
              if (
                !anomaly &&
                !items.some((item: { enabled: boolean }) => item.enabled)
              )
                throw new Error("至少需要一个启用的账号档位");
              const names = items.map((item: { name: string }) =>
                item.name?.trim().toLocaleLowerCase(),
              );
              if (new Set(names).size !== names.length)
                throw new Error("选项名称不能重复");
            },
          },
        ]}
      >
        {(fields, { add, remove }, { errors }) => (
          <>
            {fields.map((field) => (
              <div
                className={`option-row${anomaly ? " anomaly-option-row" : ""}`}
                key={field.key}
              >
                <Form.Item name={[field.name, "id"]} hidden>
                  <Input />
                </Form.Item>
                <Form.Item
                  className="option-name"
                  name={[field.name, "name"]}
                  label={`${title}名称`}
                  rules={[
                    {
                      required: true,
                      whitespace: true,
                      max: 80,
                      message: "请输入 1–80 字的名称",
                    },
                  ]}
                >
                  <Input maxLength={80} />
                </Form.Item>
                {anomaly && (
                  <Form.Item
                    className="option-interval"
                    name={[field.name, "cooldown_hours"]}
                    label="恢复间隔（小时）"
                  >
                    <InputNumber
                      min={1}
                      max={8760}
                      precision={0}
                      placeholder="沿用全局"
                      style={{ width: "100%" }}
                    />
                  </Form.Item>
                )}
                <Form.Item
                  className="option-enabled"
                  name={[field.name, "enabled"]}
                  label="状态"
                  valuePropName="checked"
                >
                  <Switch checkedChildren="启用" unCheckedChildren="停用" />
                </Form.Item>
                <Button
                  className="option-remove"
                  danger
                  onClick={() => remove(field.name)}
                  aria-label={`删除${title} ${form.getFieldValue(["account_options", kind, field.name, "name"]) || field.name + 1}`}
                >
                  删除
                </Button>
              </div>
            ))}
            <Form.ErrorList errors={errors} />
            <Button
              icon={<PlusOutlined aria-hidden="true" />}
              disabled={fields.length >= 50}
              onClick={() =>
                add({
                  // getRandomValues also works on plain-HTTP deployments.
                  id: Array.from(
                    crypto.getRandomValues(new Uint8Array(16)),
                    (byte) => byte.toString(16).padStart(2, "0"),
                  ).join(""),
                  name: "",
                  enabled: true,
                  ...(anomaly ? { cooldown_hours: null } : {}),
                })
              }
            >
              新增{title}
            </Button>
          </>
        )}
      </Form.List>
      <p className="option-help muted">
        已被账号使用的选项请停用；未被使用的选项可删除。保存后生效。
      </p>
    </section>
  );
}
