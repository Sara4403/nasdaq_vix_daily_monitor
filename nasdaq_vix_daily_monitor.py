# -*- coding: utf-8 -*-
"""
纳斯达克指数每日回撤 + VIX 区间监控

功能
- 获取纳斯达克100指数 (^NDX) 最近 6 个月日线数据
- 计算当前价距近半年高点的回撤
- 判断是否触发 10% / 15% / 20% / 25% / 30% 回撤阈值
- 获取 VIX (^VIX) 当前值并给出区间提醒
- 输出 Markdown 报告与 CSV 结果

默认输出
- nasdaq_vix_monitor_summary.csv
- nasdaq_vix_monitor_report.md
- nasdaq_vix_daily_data.csv
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import yfinance as yf


BASE_DIR = Path(__file__).resolve().parent
SUMMARY_OUT = BASE_DIR / "nasdaq_vix_monitor_summary.csv"
REPORT_OUT = BASE_DIR / "nasdaq_vix_monitor_report.md"
DATA_OUT = BASE_DIR / "nasdaq_vix_daily_data.csv"

QQ_SMTP_HOST = "smtp.qq.com"
QQ_SMTP_PORT = 465


@dataclass
class MonitorResult:
    display_name: str
    latest_date: str
    latest_close: float
    recent_low_date: str
    recent_low_close: float
    rally_from_low: float
    rally_zone: str
    rally_hits: str
    high_6m_date: str
    high_6m_close: float
    drawdown: float
    drawdown_hits: str
    vix_latest: float
    vix_signal: str


def download_daily_history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False)
    if df.empty:
        raise RuntimeError(f"{symbol} 没有下载到数据")

    # yfinance 在某些环境下会返回多层列名，这里统一拍平，避免后续取值变成 Series
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    df = df.reset_index()
    if "Date" not in df.columns and "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "Date"})
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def calc_drawdown_from_recent_high(df: pd.DataFrame) -> tuple[pd.Timestamp, float, pd.Timestamp, float, float]:
    work = df[["Date", "Close"]].dropna().copy()
    work["Close"] = pd.to_numeric(work["Close"], errors="coerce")
    work = work.dropna(subset=["Close"])

    latest_row = work.iloc[-1]
    high_idx = work["Close"].idxmax()
    high_row = work.loc[high_idx]
    latest_close = float(latest_row["Close"])
    high_close = float(high_row["Close"])
    drawdown = (high_close - latest_close) / high_close if high_close else 0.0
    return latest_row["Date"], latest_close, high_row["Date"], high_close, drawdown


def calc_rally_from_recent_low(df: pd.DataFrame) -> tuple[pd.Timestamp, float, float, str]:
    work = df[["Date", "Close"]].dropna().copy()
    work["Close"] = pd.to_numeric(work["Close"], errors="coerce")
    work = work.dropna(subset=["Close"])

    latest_row = work.iloc[-1]
    low_idx = work["Close"].idxmin()
    low_row = work.loc[low_idx]
    latest_close = float(latest_row["Close"])
    low_close = float(low_row["Close"])
    rally = (latest_close - low_close) / low_close if low_close else 0.0
    hits = rally_threshold_hits(rally)
    zone = rally_zone(rally)
    return low_row["Date"], low_close, rally, zone, hits


def drawdown_threshold_hits(drawdown: float) -> str:
    thresholds = [0.10, 0.15, 0.20, 0.25, 0.30]
    hits = [f">={int(t * 100)}%" for t in thresholds if drawdown >= t]
    return ", ".join(hits) if hits else "未达到10%"


def rally_threshold_hits(rally: float) -> str:
    thresholds = [0.10, 0.15, 0.20, 0.25, 0.30]
    hits = [f">={int(t * 100)}%" for t in thresholds if rally >= t]
    return ", ".join(hits) if hits else "未达到10%"


def rally_zone(rally: float) -> str:
    if rally >= 0.25:
        return "减仓区"
    if rally >= 0.10:
        return "观察区"
    return "加仓区"


def rally_signal(rally: float) -> str:
    zone = rally_zone(rally)
    if zone == "减仓区":
        return "涨幅已较大，建议考虑减仓"
    if zone == "观察区":
        return "涨幅进入观察区，注意仓位管理"
    return "涨幅仍较低，属于加仓区"


def vix_signal(vix_value: float) -> str:
    if vix_value >= 30:
        return "VIX ≥ 30：历史上往往是高波动 / 高赔率区"
    if 25 <= vix_value < 30:
        return "VIX 处于 25-30 区间：适合更积极的分批加仓"
    return "VIX < 25：波动相对正常，等待更好的区间"


def build_report(result: MonitorResult) -> str:
    lines = [
        "# 纳斯达克每日回撤 + VIX 监控报告",
        "",
        f"监控时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"标的：{result.display_name}",
        f"最新日期：{result.latest_date}",
        f"最新收盘：{result.latest_close:.2f}",
        f"近半年低点日期：{result.recent_low_date}",
        f"近半年低点：{result.recent_low_close:.2f}",
        f"相对近半年低点涨幅：{result.rally_from_low:.2%}",
        f"涨幅区间：{rally_zone(result.rally_from_low)}",
        f"涨幅阈值：{result.rally_hits}",
        f"提示：{rally_signal(result.rally_from_low)}",
        f"近半年高点日期：{result.high_6m_date}",
        f"近半年高点：{result.high_6m_close:.2f}",
        f"当前回撤：{result.drawdown:.2%}",
        f"回撤阈值：{result.drawdown_hits}",
        f"VIX 最新值：{result.vix_latest:.2f}",
        f"VIX 提示：{result.vix_signal}",
        "",
        "## 结论",
        f"纳指相对低点涨幅：{result.rally_from_low:.2%}",
        f"纳指相对高点回撤：{result.drawdown:.2%}",
        f"VIX：{result.vix_latest:.2f}",
        f"提醒：{result.rally_zone}；{result.rally_hits}；{result.drawdown_hits}；{result.vix_signal}；{rally_signal(result.rally_from_low)}",
    ]
    return "\n".join(lines)


def build_email_subject(result: MonitorResult) -> str:
    return f"纳指涨幅 {result.rally_from_low:.1%} / 回撤 {result.drawdown:.1%} | 每日监控"


def build_email_body(result: MonitorResult, report: str) -> str:
    return (
        f"纳斯达克每日监控结果\n\n"
        f"最新日期：{result.latest_date}\n"
        f"最新收盘：{result.latest_close:.2f}\n"
        f"近半年低点：{result.recent_low_close:.2f} ({result.recent_low_date})\n"
        f"相对低点涨幅：{result.rally_from_low:.2%}\n"
        f"涨幅区间：{result.rally_zone}\n"
        f"涨幅阈值：{result.rally_hits}\n"
        f"提示：{rally_signal(result.rally_from_low)}\n"
        f"近半年高点：{result.high_6m_close:.2f} ({result.high_6m_date})\n"
        f"当前回撤：{result.drawdown:.2%}\n"
        f"回撤阈值：{result.drawdown_hits}\n"
        f"VIX：{result.vix_latest:.2f}\n"
        f"VIX 提示：{result.vix_signal}\n\n"
        f"报告正文：\n{report}\n"
    )


def send_qq_email(subject: str, body: str) -> None:
    sender = os.getenv("QQ_EMAIL_SENDER")
    auth_code = os.getenv("QQ_EMAIL_AUTH_CODE")
    receiver = os.getenv("QQ_EMAIL_RECEIVER")

    if not sender or not auth_code or not receiver:
        print("未配置 QQ 邮箱环境变量，跳过邮件发送。")
        return

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL(QQ_SMTP_HOST, QQ_SMTP_PORT) as smtp:
        smtp.login(sender, auth_code)
        smtp.sendmail(sender, [receiver], msg.as_string())

    print(f"QQ 邮箱已发送到：{receiver}")


def main() -> None:
    index_symbol = "^NDX"
    vix_symbol = "^VIX"

    print("=== 纳斯达克100每日回撤 + VIX 监控 ===")
    print("纳指：纳斯达克100指数")
    print("VIX：波动率指数")

    ndx = download_daily_history(index_symbol, period="6mo")
    vix = download_daily_history(vix_symbol, period="6mo")

    latest_date, latest_close, high_date, high_close, drawdown = calc_drawdown_from_recent_high(ndx)
    low_date, low_close, rally_from_low, rally_zone_value, rally_hits = calc_rally_from_recent_low(ndx)
    vix_latest = float(vix[["Date", "Close"]].dropna().iloc[-1]["Close"])

    result = MonitorResult(
        display_name="纳斯达克100指数",
        latest_date=str(latest_date.date()),
        latest_close=latest_close,
        recent_low_date=str(low_date.date()),
        recent_low_close=low_close,
        rally_from_low=rally_from_low,
        rally_zone=rally_zone_value,
        rally_hits=rally_hits,
        high_6m_date=str(high_date.date()),
        high_6m_close=high_close,
        drawdown=drawdown,
        drawdown_hits=drawdown_threshold_hits(drawdown),
        vix_latest=vix_latest,
        vix_signal=vix_signal(vix_latest),
    )

    summary_df = pd.DataFrame([
        {
            "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "display_name": result.display_name,
            "latest_date": result.latest_date,
            "latest_close": result.latest_close,
            "recent_low_date": result.recent_low_date,
            "recent_low_close": result.recent_low_close,
            "rally_from_low": result.rally_from_low,
            "rally_hits": result.rally_hits,
            "high_6m_date": result.high_6m_date,
            "high_6m_close": result.high_6m_close,
            "drawdown": result.drawdown,
            "drawdown_hits": result.drawdown_hits,
            "vix_latest": result.vix_latest,
            "vix_signal": result.vix_signal,
        }
    ])

    combined = pd.DataFrame({
        "Date": ndx["Date"],
        "IXIC_Close": pd.to_numeric(ndx["Close"], errors="coerce"),
        "VIX_Close": pd.to_numeric(vix["Close"].reindex(ndx.index), errors="coerce"),
    })
    combined.to_csv(DATA_OUT, index=False, encoding="utf-8-sig")
    summary_df.to_csv(SUMMARY_OUT, index=False, encoding="utf-8-sig")

    report = build_report(result)
    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        f.write(report)

    email_subject = build_email_subject(result)
    email_body = build_email_body(result, report)
    send_qq_email(email_subject, email_body)

    print(report)
    print(f"\n已保存：{SUMMARY_OUT}")
    print(f"已保存：{REPORT_OUT}")
    print(f"已保存：{DATA_OUT}")


if __name__ == "__main__":
    main()
