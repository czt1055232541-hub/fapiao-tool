"""配置装载与凭据解析。

授权码只从 DSH 配置库读取；任何日志、异常、归档文件都不得包含它。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULTS: dict = {
    "mail": {
        "credentialSource": "dsh-qqmail",
        "credentialPath": "",
        "mailbox": "INBOX",
        "windowMode": "rolling-21-to-20",
        "extraLookbackDays": 0,
        "maxMailsPerRun": 3000,
        "onlyWithAttachments": True,
    },
    "archive": {
        "root": "",
        "folderTemplate": "{runDate}月度发票归集({total:.2f})",
        "midMonthTemplate": "{runDate}月度发票归集",
        "exclusionsFile": "",
        "consolidateRedundant": True,
        "keepXml": True,
        "keepOfd": False,
        "includeSinceYear": None,
    },
    "wechat": {
        "enabled": True,
        "inboundDir": "",
        "acceptExtensions": [".pdf"],
    },
    "classify": {
        "buyers": [],
        "homeCity": "杭州",
        "tripGapDays": 4,
        # 开支项目推断表：按「开具公司 / 项目名称」匹配，第一个命中的胜出；没命中的不猜。
        "expenseItems": [],
        # 单张打车费超过这个数（元），汇总材料里必须附行程单。
        "taxiItineraryThreshold": 200,
    },
    "report": {
        "summaryName": "00_审计摘要.json",
        "excelName": "00_发票审计明细及汇总.xlsx",
        "mergedFolder": "00_汇总发票",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


PLACEHOLDER = re.compile(r"^<.*>$")


def check_ready(cfg: dict) -> list[str]:
    """检查配置是不是还停在模板状态。

    新同事克隆下来会拿到 config.example.json，里面全是 <占位符>。必须提醒他填，
    否则工具会老老实实往一个字面叫 "<归档根目录>" 的文件夹里写——不报错，但全是垃圾。
    """
    issues: list[str] = []
    root = str((cfg.get("archive") or {}).get("root") or "").strip()
    if not root or PLACEHOLDER.match(root):
        issues.append("archive.root  —— 归档放哪，例如 D:\\发票归档")
    credential = str((cfg.get("mail") or {}).get("credentialPath") or "").strip()
    if not credential or PLACEHOLDER.match(credential):
        issues.append("mail.credentialPath  —— 邮箱凭据 JSON 的路径（见 README「准备邮箱凭据」）")
    wechat = cfg.get("wechat") or {}
    if wechat.get("enabled"):
        inbound = str(wechat.get("inboundDir") or "").strip()
        if not inbound or PLACEHOLDER.match(inbound):
            issues.append("wechat.inboundDir  —— 微信入站目录；不用微信就把 wechat.enabled 改成 false")
    return issues


def load_config(path: Path | str | None = None) -> dict:
    # 显式给路径就用它；否则优先本机 config.json，没有就退回入库的脱敏模板。
    if path:
        cfg_path = Path(path)
    else:
        cfg_path = DEFAULT_CONFIG_PATH
        if not cfg_path.is_file():
            cfg_path = DEFAULT_CONFIG_PATH.with_name("config.example.json")
    if not cfg_path.is_file():
        raise FileNotFoundError(f"配置文件不存在：{cfg_path}")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg = _deep_merge(DEFAULTS, raw)
    cfg["_config_path"] = str(cfg_path)
    return cfg


@dataclass
class Credentials:
    email: str
    auth_code: str = field(repr=False)
    imap_host: str
    imap_port: int
    imap_secure: bool
    source: str

    def redacted(self) -> str:
        return f"{self.email} @ {self.imap_host}:{self.imap_port}（授权码已读取，{len(self.auth_code)} 位，不显示）"


PRESET_SERVERS = {
    "qq": ("imap.qq.com", 993, True),
    "qq-exmail": ("imap.exmail.qq.com", 993, True),
    "163": ("imap.163.com", 993, True),
    "126": ("imap.126.com", 993, True),
    "gmail": ("imap.gmail.com", 993, True),
    "outlook": ("outlook.office365.com", 993, True),
}


def _candidate_credential_paths(cfg: dict) -> list[Path]:
    """候选凭据路径。

    **明确指定了 credentialPath 就只认它**，文件不存在就报错，不静默回退。
    否则在共用电脑上会拿着别人（或自己另一个工作区）的邮箱配置跑起来还能"自检通过"
    —— 实测踩过：指定了一个不存在的路径，工具转头读了 DSH 的默认配置，读到了别人的邮箱。
    """
    explicit = str((cfg.get("mail") or {}).get("credentialPath") or "").strip()
    if explicit:
        return [Path(explicit)]
    paths: list[Path] = []
    dsh_home = os.environ.get("DSH_HOME")
    if dsh_home:
        paths.append(Path(dsh_home) / "dsh-qqmail.json")
    paths.append(Path.home() / ".dsh" / "dsh-qqmail.json")
    return paths


def resolve_credentials(cfg: dict) -> Credentials:
    """从 DSH 的 dsh-qqmail.json 读取邮箱与授权码。"""
    errors: list[str] = []
    for path in _candidate_credential_paths(cfg):
        if not path.is_file():
            errors.append(f"未找到 {path}")
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        email = (raw.get("email") or "").strip()
        auth_code = (raw.get("authCode") or "").strip()
        if not email or not auth_code:
            errors.append(f"{path} 缺少 email 或 authCode")
            continue
        preset = (raw.get("preset") or "").strip() or _guess_preset(email)
        host = (raw.get("imapHost") or "").strip()
        port = int(raw.get("imapPort") or 0)
        secure = raw.get("imapSecure")
        if not host or not port:
            default_host, default_port, default_secure = PRESET_SERVERS.get(
                preset, PRESET_SERVERS["qq"]
            )
            host = host or default_host
            port = port or default_port
            if secure is None:
                secure = default_secure
        return Credentials(
            email=email,
            auth_code=auth_code,
            imap_host=host,
            imap_port=port,
            imap_secure=bool(True if secure is None else secure),
            source=str(path),
        )
    raise RuntimeError(
        "未能解析邮箱凭据，已尝试：\n  " + "\n  ".join(errors) + "\n"
        "请在 config.json 的 mail.credentialPath 指向一个这样的 JSON：\n"
        '  {"email": "you@qq.com", "authCode": "十六位授权码"}\n'
        "（QQ 邮箱网页版 设置 → 账号 → 开启 IMAP/SMTP 服务 后生成授权码，不是登录密码）"
    )


def _guess_preset(email: str) -> str:
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in {"qq.com", "foxmail.com"}: 
        return "qq"
    if domain in PRESET_SERVERS:
        return domain
    return "qq"


def buyer_tax_ids(cfg: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for buyer in (cfg.get("classify") or {}).get("buyers") or []:
        for tax_id in buyer.get("taxIds") or []:
            out[str(tax_id).strip().upper()] = buyer["name"]
    return out


def buyer_keywords(cfg: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for buyer in (cfg.get("classify") or {}).get("buyers") or []:
        for word in buyer.get("keywords") or []:
            out.append((str(word), buyer["name"]))
        if buyer.get("fullName"):
            out.append((str(buyer["fullName"]), buyer["name"]))
    return out
