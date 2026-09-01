import yfinance as yf
import math
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta
import pytz
import os
import sys
from dataclasses import dataclass
from typing import Optional

# --- 1. 基础配置 ---
MARKETS = {
    'US': [
        {'symbol': '^DJI',  'name': '道指', 'full_name': '道指', 'tz': 'America/New_York'},
        {'symbol': '^GSPC', 'name': '标普500', 'full_name': '标普500指数', 'tz': 'America/New_York'},
        {'symbol': '^IXIC', 'name': '纳指', 'full_name': '纳指', 'tz': 'America/New_York'}
    ],
    'EU': [
        {'symbol': '^FTSE', 'name': '英国富时', 'full_name': '英国富时100指数', 'country': '英国', 'tz': 'Europe/London'},
        {'symbol': '^FCHI', 'name': '法国CAC',  'full_name': '法国CAC40指数', 'country': '法国', 'tz': 'Europe/Paris'},
        {'symbol': '^GDAXI', 'name': '德国DAX',  'full_name': '德国DAX指数', 'country': '德国', 'tz': 'Europe/Berlin'}
    ]
}

# 【新增】历史库昨收价和快照库昨收价允许的最大偏差。
# 超过 0.01% 时，说明 Yahoo history 可能漏掉了一个交易日，改用快照昨收计算涨跌幅。
PREV_CLOSE_DRIFT_THRESHOLD = 0.0001

# --- 2. 核心数据架构 (17字段宽表) ---
@dataclass
class MarketRecord:
    # 1. 元数据层
    index_name: str
    target_date: datetime.date
    
    # 2. Engine A: 历史事实层
    hist_t_close: Optional[float] = None
    hist_t1_date: Optional[datetime.date] = None
    hist_t1_close: Optional[float] = None
    
    # 3. Engine B: 快照事实层
    snap_date: Optional[datetime.date] = None
    snap_last_price: Optional[float] = None
    snap_prev_close: Optional[float] = None
    
    # 4. 衍生计算层
    calc_hist_pct: Optional[float] = None
    calc_snap_pct: Optional[float] = None
    calc_hybrid_pct: Optional[float] = None
    diff_hist_vs_snap: Optional[float] = None
    diff_hist_pct_vs_hybrid_pct: Optional[float] = None 
    diff_hist_prev_vs_snap_prev: Optional[float] = None # 【新增】：昨收价漂移率
    
    # 5. 仲裁结果层
    final_status: str = "PENDING"
    final_close: Optional[float] = None
    final_change_pct: Optional[float] = None

    def print_record(self):
        """格式化打印这条宽表记录，便于一目了然地查错"""
        print(f"\n[{self.index_name}] 📊 --- 核心数据宽表 (Data Record) ---")
        print(f"  [Meta] 目标日: {self.target_date}")
        print(f"  [Hist] T日收盘: {self.hist_t_close} | T-1日({self.hist_t1_date}): {self.hist_t1_close}")
        
        # 【新增打印】：将昨收差值率接在 Snap 打印行后方
        diff_prev_p = f"{self.diff_hist_prev_vs_snap_prev*100:.4f}%" if self.diff_hist_prev_vs_snap_prev is not None else "None"
        print(f"  [Snap] 真实戳: {self.snap_date} | 最新: {self.snap_last_price} | 自带昨收: {self.snap_prev_close} | 昨收差异率: {diff_prev_p}")
        
        # 格式化百分比显示
        hp = f"{self.calc_hist_pct*100:+.4f}%" if self.calc_hist_pct is not None else "None"
        hyp = f"{self.calc_hybrid_pct*100:+.4f}%" if self.calc_hybrid_pct is not None else "None"
        diff_p = f"{self.diff_hist_pct_vs_hybrid_pct*100:+.4f}%" if self.diff_hist_pct_vs_hybrid_pct is not None else "None"
        final_p = f"{self.final_change_pct*100:+.4f}%" if self.final_change_pct is not None else "None"
        
        print(f"  [Calc] 纯历史涨幅: {hp} | 跨源混合涨幅: {hyp} | 涨幅差值(历史-混合): {diff_p}")
        print(f"  [Output] 🏁 仲裁状态: {self.final_status} | 最终实际采用涨幅: {final_p}")
        print("-" * 65)

def get_us_eastern_target_date():
    """获取绝对的交易目标日期 (美东锚点)"""
    input_date = os.environ.get('INPUT_TEST_DATE')
    if input_date and input_date.strip():
        try:
            return datetime.strptime(input_date.strip(), "%Y-%m-%d").date()
        except ValueError: pass

    us_eastern = pytz.timezone('US/Eastern')
    now_est = datetime.now(us_eastern)
    target_date = now_est.date()
    
    if now_est.weekday() == 5: target_date -= timedelta(days=1)
    elif now_est.weekday() == 6: target_date -= timedelta(days=2)

    return target_date

def safe_float(value):
    """【新增】把 Yahoo 返回的 NaN/空值过滤掉，避免 nan 被当作有效收盘价参与计算。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None

def process_market(market_info, target_date):
    """三段式流控：采掘 (Fetch) -> 衍生计算 (Compute) -> 仲裁 (Arbitrate)"""
    symbol = market_info['symbol']
    name = market_info['name']
    market_tz = pytz.timezone(market_info['tz'])
    
    print(f"\n" + "="*50)
    print(f"🔍 启动双擎流水线: {name} ({symbol}) | 目标: {target_date}")
    print("="*50)
    
    rec = MarketRecord(index_name=name, target_date=target_date)
    ticker = yf.Ticker(symbol)
    
    # ---------------------------------------------------------
    # Stage 1: 盲目采掘 (Fetch Raw Data)
    # ---------------------------------------------------------
    
    # [Engine A: History]
    try:
        df = ticker.history(start=target_date - timedelta(days=5), end=target_date + timedelta(days=3))
        if not df.empty:
            df.index = [d.date() for d in df.index]
            all_dates = df.index.tolist()
            print(f"[{name}] 🧭 历史库日期序列: {all_dates}")  # 【新增】排查 Yahoo history 是否漏日
            
            if target_date in all_dates:
                rec.hist_t_close = safe_float(df.loc[target_date]['Close'])
                idx = all_dates.index(target_date)
                if idx > 0:
                    rec.hist_t1_date = all_dates[idx-1]
                    rec.hist_t1_close = safe_float(df.iloc[idx-1]['Close'])
                print(f"[{name}] 🟢 历史库: 完美拉取 T 日与 T-1 日数据。")
            else:
                past_dates = [d for d in all_dates if d < target_date]
                if past_dates:
                    rec.hist_t1_date = past_dates[-1]
                    rec.hist_t1_close = safe_float(df.loc[rec.hist_t1_date]['Close'])
                print(f"[{name}] 🟡 历史库: T日缺失。但成功截获 T-1 日({rec.hist_t1_date}) 数据作备用。")
    except Exception as e:
        print(f"[{name}] 🔴 历史库异常: {e}")

    # [Engine B: Snapshot]
    try:
        fast = ticker.fast_info
        trade_ts = ticker.info.get('regularMarketTime')
        if trade_ts:
            trade_dt = datetime.fromtimestamp(trade_ts, pytz.utc).astimezone(market_tz)
            rec.snap_date = trade_dt.date()
            rec.snap_last_price = safe_float(fast.last_price)
            rec.snap_prev_close = safe_float(fast.previous_close)
            print(f"[{name}] 🔵 快照库: 探针定位于当地时间 {trade_dt.strftime('%m-%d %H:%M:%S')}")
    except Exception as e:
        print(f"[{name}] 🔴 快照库异常: {e}")

    # ---------------------------------------------------------
    # Stage 2: 衍生计算 (Compute Metrics)
    # ---------------------------------------------------------
    # 1. 纯历史涨跌幅
    if rec.hist_t_close is not None and rec.hist_t1_close is not None:
        rec.calc_hist_pct = (rec.hist_t_close - rec.hist_t1_close) / rec.hist_t1_close
        
    # 2. 纯快照涨跌幅
    if rec.snap_last_price is not None and rec.snap_prev_close is not None:
        rec.calc_snap_pct = (rec.snap_last_price - rec.snap_prev_close) / rec.snap_prev_close
        
    # 3. 跨源混合涨跌幅
    if rec.snap_last_price is not None and rec.hist_t1_close is not None:
        rec.calc_hybrid_pct = (rec.snap_last_price - rec.hist_t1_close) / rec.hist_t1_close

    # 4. 双库差异 (用于触发熔断)
    if rec.hist_t_close is not None and rec.snap_last_price is not None:
        rec.diff_hist_vs_snap = abs(rec.hist_t_close - rec.snap_last_price) / rec.hist_t_close

    # 5. 涨跌幅偏差
    if rec.calc_hist_pct is not None and rec.calc_hybrid_pct is not None:
        rec.diff_hist_pct_vs_hybrid_pct = rec.calc_hist_pct - rec.calc_hybrid_pct
        
    # 6. 【新增】昨收价漂移率 (监控除权除息导致的雅虎后台数据修正幅度)
    if rec.hist_t1_close is not None and rec.snap_prev_close is not None:
        # 为了防止极小概率的分母为0异常
        if rec.hist_t1_close != 0:
            rec.diff_hist_prev_vs_snap_prev = abs(rec.hist_t1_close - rec.snap_prev_close) / rec.hist_t1_close

    # ---------------------------------------------------------
    # Stage 3: 智能仲裁 (Arbitrate - 四道门决策树)
    # ---------------------------------------------------------
    
    # 第一道门：完美的历史数据
    if rec.hist_t_close is not None:
        if rec.snap_date == rec.target_date:
            if rec.snap_last_price is not None and rec.diff_hist_vs_snap > 0.001:
                rec.final_status = "MISMATCH_ERROR"
            # 【新增】如果今日收盘价匹配，但历史库昨收价明显偏离快照昨收价，
            # 通常说明 history 漏掉了 T-1 交易日。此时使用“历史 T 日收盘 + 快照昨收”计算涨跌幅。
            elif (
                rec.snap_prev_close is not None
                and rec.diff_hist_prev_vs_snap_prev is not None
                and rec.diff_hist_prev_vs_snap_prev > PREV_CLOSE_DRIFT_THRESHOLD
            ):
                print(f"[{name}] 🟠 昨收修正: 历史昨收({rec.hist_t1_date})={rec.hist_t1_close} 与快照昨收={rec.snap_prev_close} 差异超过阈值，改用快照昨收计算涨跌幅。")
                rec.final_status = "HYBRID_PREV_CLOSE"
                rec.final_close = rec.hist_t_close
                rec.final_change_pct = (rec.hist_t_close - rec.snap_prev_close) / rec.snap_prev_close
            else:
                rec.final_status = "MATCH_HISTORY"
                rec.final_close = rec.hist_t_close
                rec.final_change_pct = rec.calc_hist_pct
        else:
            rec.final_status = "MATCH_HISTORY"
            rec.final_close = rec.hist_t_close
            rec.final_change_pct = rec.calc_hist_pct

    # 第二道门：触发跨源缝合
    elif rec.hist_t_close is None and rec.hist_t1_close is not None and rec.snap_date == rec.target_date:
        rec.final_status = "HYBRID_FALLBACK"
        rec.final_close = rec.snap_last_price
        rec.final_change_pct = rec.calc_hybrid_pct
        
    # 第三道门：极致兜底
    elif rec.hist_t_close is None and rec.hist_t1_close is None and rec.snap_date == rec.target_date:
        rec.final_status = "PURE_SNAPSHOT"
        rec.final_close = rec.snap_last_price
        rec.final_change_pct = rec.calc_snap_pct
        
    # 第四道门：确认休市
    elif rec.hist_t_close is None and (rec.snap_date is None or rec.snap_date < rec.target_date):
        rec.final_status = "HOLIDAY"
        
    else:
        rec.final_status = "UNKNOWN_ERROR"

    rec.print_record()
    return rec

# --- 辅助与发送函数 ---
def format_change_text(name, change_amt, change_pct):
    status = "收涨" if change_amt > 0 else "收跌" if change_amt < 0 else "收平"
    return f"{name}{status}{abs(change_pct)*100:.2f}%"

def main():
    target_date = get_us_eastern_target_date()
    date_str_cn = f"{target_date.year}年{target_date.month}月{target_date.day}日"
    
    print(f"\n🚀 === 自动化日报生成流水线启动 | 美东锚点: {target_date} ===")
    
    report_data = [] 
    global_mismatch_flag = False 
    
    # --- 1. 处理美股 ---
    us_phrases = []
    us_closed_count = 0
    for m in MARKETS['US']:
        rec = process_market(m, target_date)
        
        if rec.final_status == "MISMATCH_ERROR":
            global_mismatch_flag = True
            
        if rec.final_status in ["MATCH_HISTORY", "HYBRID_PREV_CLOSE", "HYBRID_FALLBACK", "PURE_SNAPSHOT"]:
            change_amt = rec.final_close - (rec.final_close / (1 + rec.final_change_pct))
            text = format_change_text(m['full_name'], change_amt, rec.final_change_pct)
            us_phrases.append(text)
            color = "#ff0000" if change_amt > 0 else "#008000"
            report_data.append([m['full_name'], f"{rec.final_close:,.2f}", f"{change_amt:+.2f}", f"{rec.final_change_pct*100:+.2f}%", color])
        elif rec.final_status == "HOLIDAY":
            us_closed_count += 1
            report_data.append([m['full_name'], "-", "-", "因节假日休市", "gray"])

    us_summary = "美股因节假日休市" if us_closed_count == len(MARKETS['US']) else "美股" + "，".join(us_phrases)

    # --- 2. 处理欧股 ---
    eu_phrases = []
    eu_closed_count = 0
    for m in MARKETS['EU']:
        rec = process_market(m, target_date)
        
        if rec.final_status == "MISMATCH_ERROR":
            global_mismatch_flag = True
            
        if rec.final_status in ["MATCH_HISTORY", "HYBRID_PREV_CLOSE", "HYBRID_FALLBACK", "PURE_SNAPSHOT"]:
            change_amt = rec.final_close - (rec.final_close / (1 + rec.final_change_pct))
            text = format_change_text(m['full_name'], change_amt, rec.final_change_pct)
            eu_phrases.append(text)
            color = "#ff0000" if change_amt > 0 else "#008000"
            report_data.append([m['full_name'], f"{rec.final_close:,.2f}", f"{change_amt:+.2f}", f"{rec.final_change_pct*100:+.2f}%", color])
        elif rec.final_status == "HOLIDAY":
            eu_closed_count += 1
            eu_phrases.append(f"{m['country']}因节假日休市")
            report_data.append([m['full_name'], "-", "-", "因节假日休市", "gray"])

    eu_summary = "欧洲方面因节假日休市" if eu_closed_count == len(MARKETS['EU']) else "欧洲方面，" + "，".join(eu_phrases)

    # --- 3. 熔断与全局拦截 ---
    if global_mismatch_flag:
        print("\n" + "❌"*20)
        print("🚨 触发全局熔断！双引擎数据发生严重分歧，已主动阻断发信防止假数据外泄。")
        print("❌"*20 + "\n")
        sys.exit(1)

    if us_closed_count + eu_closed_count == len(MARKETS['US']) + len(MARKETS['EU']):
        print(f"\n💤 结论: {target_date} 全球主要市场均因节假日休市，跳过邮件发送流程。")
        return

    # --- 4. 生成最终文案并发送 ---
    final_text = f"境外股市运行情况。当地时间{date_str_cn}，{us_summary}。{eu_summary}。"
    
    print("\n" + "="*40)
    print("📝 最终报表摘要 (准备发信)：")
    print(final_text)
    print("="*40 + "\n")

    subject = f"境外股市运行情况-{date_str_cn}"
    send_email_html(subject, final_text, report_data, date_str_cn)

def send_email_html(subject, summary, table_rows, date_str):
    sender = os.environ.get('MAIL_USERNAME', '').strip()
    password = os.environ.get('MAIL_PASSWORD', '').strip()
    smtp_server = os.environ.get('MAIL_SERVER', '').strip()
    
    if not sender or not password:
        print("⚠️ 发信环节跳过：未检测到 MAIL_USERNAME 或 MAIL_PASSWORD (纯本地测试模式)。")
        return

    receivers_str = os.environ.get('MAIL_RECEIVER', '')
    receivers = [r.strip() for r in receivers_str.split(',') if r.strip()]
    smtp_port = int(os.environ.get('MAIL_PORT', 587))

    email_body = f"<div style=\"font-family: Arial, sans-serif; color: #333; line-height: 1.8;\"><p>{summary}</p></div>"
    msg = MIMEText(email_body, 'html', 'utf-8')
    msg['From'] = formataddr(("境外股市情况", sender))
    msg['To'] = ",".join(receivers)
    msg['Subject'] = Header(subject, 'utf-8')

    try:
        print(f"📧 连接 {smtp_server}:{smtp_port} 并发送至 {len(receivers)} 个订阅者...")
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("✅ 邮件群发成功！")
    except Exception as e:
        print(f"❌ 发信失败: {e}")

if __name__ == "__main__":
    main()
