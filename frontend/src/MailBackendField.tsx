import { useEffect, useRef } from "react";
import { Form, Select } from "antd";
import { useResource } from "./ui";

type Backends = {
  default: string | null;
  items: { id: string; name: string }[];
};

export function MailBackendField({
  useDefault = false,
}: {
  useDefault?: boolean;
}) {
  const form = Form.useFormInstance();
  const initialized = useRef(false);
  const { data, error } = useResource<Backends>("/mail-backends", 0);
  useEffect(() => {
    if (data && useDefault && !initialized.current) {
      initialized.current = true;
      if (
        form.getFieldValue("mail_backend") === undefined &&
        !form.isFieldTouched("mail_backend")
      )
        form.setFieldValue("mail_backend", data.default);
    }
  }, [data, form, useDefault]);
  return (
    <Form.Item
      name="mail_backend"
      label="邮箱后端"
      extra={error || "选择不启用后，用户仍可查看密码，邮箱验证码需手动获取。"}
      getValueProps={(value) => ({ value: value === null ? "" : value })}
      getValueFromEvent={(value) => value || null}
      rules={[
        {
          validator: async (_, value) => {
            if (!data) throw new Error("邮箱后端列表尚未加载，请稍后重试");
            if (value !== null && !data.items.some((p) => p.id === value))
              throw new Error("请选择可用的邮箱后端，或选择不启用");
          },
        },
      ]}
    >
      <Select
        loading={!data && !error}
        disabled={!data}
        allowClear
        placeholder="选择邮箱后端"
        options={[
          { value: "", label: "不启用自动取码" },
          ...(data?.items || []).map((p) => ({ value: p.id, label: p.name })),
        ]}
      />
    </Form.Item>
  );
}
