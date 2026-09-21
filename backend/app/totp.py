"""QR validation and server-clock TOTP generation. No image or secret is logged."""

import io
import warnings
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pyotp
import zxingcpp
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 8_000_000


class InvalidQR(ValueError):
    pass


def parse_uri(uri):
    try:
        if len(uri) > 2048:
            raise ValueError()
        url = urlsplit(uri)
        query = parse_qs(url.query, keep_blank_values=True)
        if (
            url.scheme != "otpauth"
            or url.netloc != "totp"
            or url.fragment
            or any(len(v) != 1 for v in query.values())
            or set(query) - {"secret", "issuer", "algorithm", "digits", "period"}
        ):
            raise ValueError()
        value = pyotp.parse_uri(uri)
        if not isinstance(value, pyotp.TOTP) or value.digits not in {6, 7, 8}:
            raise ValueError()
        if not 1 <= value.interval <= 300 or not 16 <= len(value.secret) <= 256:
            raise ValueError()
        value.at(0)  # Also validate base32 and the selected digest.
        return value
    except Exception:
        raise InvalidQR("二维码必须包含有效的 TOTP 配置（不支持 HOTP）") from None


def decode_image(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise InvalidQR("二维码图片不得超过 1 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise InvalidQR("请上传 PNG、JPEG 或 WebP 图片")
                if (
                    source.width * source.height > MAX_IMAGE_PIXELS
                    or getattr(source, "n_frames", 1) != 1
                ):
                    raise InvalidQR("图片不得超过 800 万像素，且不能是动图")
                codes = zxingcpp.read_barcodes(
                    source.convert("RGB"), formats=zxingcpp.BarcodeFormat.QRCode
                )
        if len(codes) != 1 or not codes[0].valid:
            raise InvalidQR("图片必须包含且仅包含一个清晰的二维码")
        uri = codes[0].text
        parse_uri(uri)
        return uri
    except InvalidQR:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise InvalidQR("图片无法读取，请重新上传清晰的二维码") from None


def current_code(uri, stamp):
    value = parse_uri(uri)
    seconds = stamp.timestamp()
    valid_until = (int(seconds) // value.interval + 1) * value.interval
    return {
        "code": value.at(stamp),
        "server_time": stamp,
        "valid_until": datetime.fromtimestamp(valid_until, timezone.utc),
        "period": value.interval,
    }
