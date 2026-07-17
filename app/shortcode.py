"""随机短码 / 编号生成。

用 secrets 生成不可预测的 base62 短码，不含连续数字、不暴露业务 ID。
"""
import secrets

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"


def gen_shortcode(length: int) -> str:
    """base62 随机短码，用于二维码短链和凭证对外编号。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def gen_digits(length: int) -> str:
    """纯数字码，用于备用核销码。"""
    return "".join(secrets.choice(_DIGITS) for _ in range(length))
