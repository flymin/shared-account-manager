"""Local-only password screening; never log or return estimator results."""

from threading import Lock

from zxcvbn import zxcvbn

# New passwords only. Login still accepts historical passwords up to 256 chars.
MIN_LENGTH = 15
MAX_LENGTH = 72
MIN_SCORE = 3
_estimate_lock = Lock()


def password_error(password, *, username="", display_name=""):
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        return f"新密码须为 {MIN_LENGTH}–{MAX_LENGTH} 个字符"
    inputs = [username, display_name]
    inputs.extend(username.split("@", 1))
    inputs.extend(display_name.split())
    # Keep the library's length bound: pathological long inputs are expensive.
    # The result contains plaintext and matched substrings; only inspect score.
    inputs.extend(["account manager", "account-manager", "service"])
    inputs = list(dict.fromkeys(v.lower() for v in inputs if v))
    # zxcvbn updates its shared user-input dictionary while evaluating a password.
    with _estimate_lock:
        score = zxcvbn(password, user_inputs=inputs)["score"]
    if score < MIN_SCORE:
        return "密码过于容易猜测，请使用更长的随机密码或多个无关词组成的口令，避免常见密码、重复、连续字符及姓名或用户名"
    return None
