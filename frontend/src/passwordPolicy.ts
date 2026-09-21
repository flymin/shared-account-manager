export const PASSWORD_MIN_LENGTH = 15;
export const PASSWORD_MAX_LENGTH = 72;
export const PASSWORD_HINT = `使用 ${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 个字符。推荐随机密码或多个无关词组成的长口令；常见、重复、连续字符及易猜的用户名组合会被拒绝。`;

export function passwordRules(required = true) {
  return [
    {
      validator(_: unknown, value?: string) {
        if (!value)
          return required
            ? Promise.reject(new Error("请输入新密码"))
            : Promise.resolve();
        const length = Array.from(value).length;
        return length >= PASSWORD_MIN_LENGTH && length <= PASSWORD_MAX_LENGTH
          ? Promise.resolve()
          : Promise.reject(
              new Error(
                `密码须为 ${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 个字符`,
              ),
            );
      },
    },
  ];
}
