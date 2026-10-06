"""配置装载与凭据解析。

授权码只从 DSH 配置库读取；任何日志、异常、归档文件都不得包含它。
"""

from __future__ import annotations

import json
import os
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


def load_config(path: Path | str | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
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
    paths: list[Path] = []
    explicit = (cfg.get("mail") or {}).get("credentialPath") or ""
    if explicit:
        paths.append(Path(explicit))
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
        "请先用 dsh-qqmail 插件配置邮箱（qqmail_config：email + authCode 授权码）。"
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
