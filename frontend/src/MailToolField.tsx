import { useEffect, useRef } from "react";
import { Form, Select } from "antd";
import { useResource } from "./ui";

type MailTools = {
  default: string | null;
  items: { id: string; name: string }[];
};

export function MailToolField({
  useDefault = false,
}: {
  useDefault?: boolean;
}) {
  const form = Form.useFormInstance();
  const initialized = useRef(false);
  const { data, error } = useResource<MailTools>("/mail-tools", 0);
  useEffect(() => {
    if (data && useDefault && !initialized.current) {
      initialized.current = true;
      if (
        form.getFieldValue("mail_tool") === undefined &&
        !form.isFieldTouched("mail_tool")
      )
        form.setFieldValue("mail_tool", data.default);
    }
  }, [data, form, useDefault]);
  return (
    <Form.Item
      name="mail_tool"
      label="邮箱取码工具"
      extra={error || "选择不启用后，用户仍可查看密码，邮箱验证码需手动获取。"}
      getValueProps={(value) => ({ value: value === null ? "" : value })}
      getValueFromEvent={(value) => value || null}
      rules={[
        {
          validator: async (_, value) => {
            if (!data) throw new Error("邮箱取码工具列表尚未加载，请稍后重试");
            if (value !== null && !data.items.some((p) => p.id === value))
              throw new Error("请选择可用的邮箱取码工具，或选择不启用");
          },
        },
      ]}
    >
      <Select
        loading={!data && !error}
        disabled={!data}
        allowClear
        placeholder="选择邮箱取码工具"
        options={[
          { value: "", label: "不启用自动取码" },
          ...(data?.items || []).map((p) => ({ value: p.id, label: p.name })),
        ]}
      />
    </Form.Item>
  );
}
