import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta, time
import pytz
import os
import sys

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

def get_us_eastern_target_date():
    """
    步骤1：锁定绝对时间锚点 (强制基于美东时间)
    无论你在北京时间的早晨还是凌晨运行，此函数都会准确返回“刚刚过去的那个美东交易日”。
    """
    input_date = os.environ.get('INPUT_TEST_DATE')
    if input_date and input_date.strip():
        try:
            target = datetime.strptime(input_date.strip(), "%Y-%m-%d").date()
            print(f"🛠️ [手动测试模式] 锁定日期: {target}")
            return target
        except ValueError:
            pass

    # 获取当前的美东时间
    us_eastern = pytz.timezone('US/Eastern')
    now_est = datetime.now(us_eastern)
    target_date = now_est.date()
    
    # 如果美东时间当前是周六或周日，则目标交易日自动回退到周五
    if now_est.weekday() == 5: # 周六
        target_date -= timedelta(days=1)
    elif now_est.weekday() == 6: # 周日
        target_date -= timedelta(days=2)

    print(f"⏱️ 运行环境时间: 本地={datetime.now()} | 美东={now_est.strftime('%Y-%m-%d %H:%M:%S')}")
    return target_date

def get_market_data_dual_engine(market_info, target_date):
    """
    步骤2 & 3 & 4：双擎拉取与状态仲裁
    """
    symbol = market_info['symbol']
    name = market_info['name']
    market_tz = pytz.timezone(market_info['tz'])
    
    print(f"\n--- 🔍 开始双擎校验处理: {name} ({symbol}) | 目标日期: {target_date} ---")
    ticker = yf.Ticker(symbol)
    
    hist_data = None
    snap_data = None
    
    # ==========================================
    # Engine A: 历史数据库 (History)
    # ==========================================
    try:
        df = ticker.history(start=target_date - timedelta(days=5), end=target_date + timedelta(days=3))
        if not df.empty:
            df.index = [d.date() for d in df.index]
            if target_date in df.index:
                target_row = df.loc[target_date]
                dates = df.index.tolist()
                idx = dates.index(target_date)
                prev_close = df.iloc[idx-1]['Close'] if idx > 0 else target_row['Open']
                
                hist_data = {
                    'close': target_row['Close'],
                    'prev_close': prev_close,
                    'change_amt': target_row['Close'] - prev_close,
                    'change_pct': ((target_row['Close'] - prev_close) / prev_close) * 100
                }
                print(f"[{name}] 🟢 Engine A (历史库): 成功获取到 {target_date} 数据 (收盘: {hist_data['close']:.2f})")
            else:
                print(f"[{name}] 🟡 Engine A (历史库): 无 {target_date} 数据 (可能是休市或遭遇雅虎日结延迟)")
        else:
            print(f"[{name}] 🔴 Engine A (历史库): 返回空数据")
    except Exception as e:
        print(f"[{name}] 🔴 Engine A (历史库) 异常: {e}")

    # ==========================================
    # Engine B: 实时快照库 (Snapshot / fast_info)
    # ==========================================
    try:
        # 获取快照价格
        fast = ticker.fast_info
        # 获取基础信息中的最新交易时间戳 (UTC)
        info = ticker.info
        trade_time_utc_ts = info.get('regularMarketTime')
        
        if trade_time_utc_ts:
            # 将 UTC 时间戳转换为当地市场的实际日期
            trade_dt = datetime.fromtimestamp(trade_time_utc_ts, pytz.utc).astimezone(market_tz)
            trade_date = trade_dt.date()
            
            print(f"[{name}] 🔵 Engine B (快照库): 探针发现最新交易发生于当地时间 {trade_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 核心防伪验证：快照的日期必须等于我们要的目标日期
            if trade_date == target_date:
                close_b = fast.last_price
                prev_close_b = fast.previous_close
                snap_data = {
                    'close': close_b,
                    'prev_close': prev_close_b,
                    'change_amt': close_b - prev_close_b,
                    'change_pct': ((close_b - prev_close_b) / prev_close_b) * 100
                }
                print(f"[{name}] 🟢 Engine B (快照库): 时间戳比对吻合，快照有效 (收盘: {snap_data['close']:.2f})")
            elif trade_date < target_date:
                print(f"[{name}] 🟡 Engine B (快照库): 最新快照日期({trade_date}) 早于 目标日期({target_date})，证明目标日未开盘。")
            else:
                print(f"[{name}] 🔴 Engine B (快照库): 逻辑异常，快照日期({trade_date}) 晚于 目标日期({target_date})！")
        else:
            print(f"[{name}] 🔴 Engine B (快照库): 无法获取交易时间戳，快照失效。")
    except Exception as e:
        print(f"[{name}] 🔴 Engine B (快照库) 异常: {e}")

    # ==========================================
    # ⚖️ 状态仲裁机 (Arbiter)
    # ==========================================
    final_data = None
    status_code = "" # MATCH, FALLBACK, HOLIDAY, MISMATCH, ERROR
    
    if hist_data and snap_data:
        # 场景1：双库皆有数据 -> 执行容差比对 (容差设为 0.1%)
        diff = abs(hist_data['close'] - snap_data['close']) / hist_data['close']
        if diff < 0.001:
            print(f"[{name}] 🛡️ 仲裁结果: [MATCH] 双库数据一致 (差异 {diff*100:.4f}%)。采用历史库数据。")
            final_data = hist_data
            status_code = "MATCH"
        else:
            print(f"[{name}] 🚨 仲裁结果: [MISMATCH] 严重警告！双库数据不一致！历史={hist_data['close']:.2f}, 快照={snap_data['close']:.2f}")
            status_code = "MISMATCH"
            
    elif not hist_data and snap_data:
        # 场景2：历史库由于日结延迟未返回数据，但快照库日期吻合 -> 信任快照
        print(f"[{name}] 🛡️ 仲裁结果: [FALLBACK] 触发降级保护，采用快照库数据。")
        final_data = snap_data
        status_code = "FALLBACK"
        
    elif hist_data and not snap_data:
        # 场景3：极少见，快照失效但历史库有 -> 信任历史库
        print(f"[{name}] 🛡️ 仲裁结果: [HISTORY_ONLY] 快照失效，采用历史库数据。")
        final_data = hist_data
        status_code = "MATCH"
        
    else:
        # 场景4：双库皆无有效目标日数据 -> 确认为节假日
        print(f"[{name}] 🛡️ 仲裁结果: [HOLIDAY] 确认为节假日休市。")
        final_data = None
        status_code = "HOLIDAY"

    return final_data, status_code

def format_change_text(name, data):
    status = "收涨" if data['change_amt'] > 0 else "收跌" if data['change_amt'] < 0 else "收平"
    return f"{name}{status}{abs(data['change_pct']):.2f}%"

def main():
    target_date = get_us_eastern_target_date()
    date_str_cn = f"{target_date.year}年{target_date.month}月{target_date.day}日"
    
    print(f"\n🚀 开始执行自动化报表任务，锁定交易锚点: {target_date} (美东)")
    
    report_data = [] 
    global_mismatch_flag = False # 全局熔断开关
    
    # --- 1. 处理美股 ---
    us_phrases = []
    us_closed_count = 0
    for m in MARKETS['US']:
        data, status = get_market_data_dual_engine(m, target_date)
        
        if status == "MISMATCH":
            global_mismatch_flag = True
            
        if data:
            text = format_change_text(m['full_name'], data)
            us_phrases.append(text)
            color = "#ff0000" if data['change_amt'] > 0 else "#008000"
            report_data.append([m['full_name'], f"{data['close']:,.2f}", f"{data['change_amt']:+.2f}", f"{data['change_pct']:+.2f}%", color])
        else:
            us_closed_count += 1
            report_data.append([m['full_name'], "-", "-", "因节假日休市", "gray"])

    us_summary = "美股因节假日休市" if us_closed_count == len(MARKETS['US']) else "美股" + "，".join(us_phrases)

    # --- 2. 处理欧股 ---
    eu_phrases = []
    eu_closed_count = 0
    for m in MARKETS['EU']:
        data, status = get_market_data_dual_engine(m, target_date)
        
        if status == "MISMATCH":
            global_mismatch_flag = True
            
        if data:
            text = format_change_text(m['full_name'], data)
            eu_phrases.append(text)
            color = "#ff0000" if data['change_amt'] > 0 else "#008000"
            report_data.append([m['full_name'], f"{data['close']:,.2f}", f"{data['change_amt']:+.2f}", f"{data['change_pct']:+.2f}%", color])
        else:
            eu_closed_count += 1
            eu_phrases.append(f"{m['country']}因节假日休市")
            report_data.append([m['full_name'], "-", "-", "因节假日休市", "gray"])

    eu_summary = "欧洲方面因节假日休市" if eu_closed_count == len(MARKETS['EU']) else "欧洲方面，" + "，".join(eu_phrases)

    # --- 3. 熔断与全局拦截 ---
    if global_mismatch_flag:
        print("\n" + "❌"*20)
        print("🚨 触发全局熔断机制！检测到历史库与快照库数据存在严重不一致。")
        print("为防止向订阅者发送可能存在计算错误的假数据，已主动拦截本次邮件发送流程！")
        print("建议排查原因后手动运行或等待下一个工作日自愈。")
        print("❌"*20 + "\n")
        sys.exit(1) # 直接阻断退出，GitHub Actions 会将其标红为 Failed

    if us_closed_count + eu_closed_count == len(MARKETS['US']) + len(MARKETS['EU']):
        print(f"\n💤 结论: {target_date} 全球主要市场均因节假日休市，跳过邮件发送流程。")
        return

    # --- 4. 生成最终文案并发送 ---
    final_text = f"境外股市运行情况。当地时间{date_str_cn}，{us_summary}。{eu_summary}。"
    
    print("\n" + "="*40)
    print("📝 最终生成的文字摘要 (校验通过，准备发送)：")
    print(final_text)
    print("="*40 + "\n")

    subject = f"境外股市运行情况-{date_str_cn}"
    send_email_html(subject, final_text, report_data, date_str_cn)

def send_email_html(subject, summary, table_rows, date_str):
    sender = os.environ.get('MAIL_USERNAME', '').strip()
    password = os.environ.get('MAIL_PASSWORD', '').strip()
    smtp_server = os.environ.get('MAIL_SERVER', '').strip()
    
    if not sender or not password:
        print("⚠️ 邮件发送跳过：未检测到发信环境变量 (本地测试模式)。")
        return

    receivers_str = os.environ.get('MAIL_RECEIVER', '')
    receivers = [r.strip() for r in receivers_str.split(',') if r.strip()]
    
    try:
        smtp_port = int(os.environ.get('MAIL_PORT', 587))
    except:
        smtp_port = 587

    email_body = f"""
    <div style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #333; line-height: 1.8; font-size: 16px;">
        <p>{summary}</p>
    </div>
    """

    msg = MIMEText(email_body, 'html', 'utf-8')
    msg['From'] = formataddr(("境外股市情况", sender))
    msg['To'] = ",".join(receivers)
    msg['Subject'] = Header(subject, 'utf-8')

    try:
        print(f"📧 准备连接 {smtp_server}:{smtp_port} ...")
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(sender, password)
        print(f"📧 正在发送给 {len(receivers)} 位收件人...")
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("✅ 邮件群发成功！")
    except Exception as e:
        print(f"❌ 发送失败: {e}")

if __name__ == "__main__":
    main()
