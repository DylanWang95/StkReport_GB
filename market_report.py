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

    print(f"⏱️ 运行环境时间: 本地={datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 美东={now_est.strftime('%Y-%m-%d %H:%M:%S')}")
    return target_date

def get_market_data_dual_engine(market_info, target_date):
    """
    步骤2 & 3 & 4：双擎拉取与状态仲裁 (附带详尽过程打印)
    """
    symbol = market_info['symbol']
    name = market_info['name']
    market_tz = pytz.timezone(market_info['tz'])
    
    print(f"\n" + "="*50)
    print(f"🔍 开始双擎校验处理: {name} ({symbol}) | 目标日期: {target_date}")
    print("="*50)
    
    ticker = yf.Ticker(symbol)
    hist_data = None
    snap_data = None
    
    # ==========================================
    # Engine A: 历史数据库 (History)
    # ==========================================
    try:
        start_date = target_date - timedelta(days=5)
        end_date = target_date + timedelta(days=3)
        print(f"[{name}] 🟢 Engine A (历史库): 发起API请求，拉取窗口: {start_date} 至 {end_date}")
        df = ticker.history(start=start_date, end=end_date)
        
        if not df.empty:
            df.index = [d.date() for d in df.index]
            
            # --- 打印历史库提取的原始序列 ---
            print(f"[{name}]   📊 历史库返回的有效交易日序列:")
            for d, row in df.iterrows():
                marker = " <--- [当前目标日]" if d == target_date else ""
                print(f"      > 日期: {d} | 开盘: {row['Open']:,.2f} | 收盘: {row['Close']:,.2f}{marker}")
            print(f"      " + "-" * 40)
            
            if target_date in df.index:
                target_row = df.loc[target_date]
                dates = df.index.tolist()
                idx = dates.index(target_date)
                
                # 寻找基准价判断逻辑
                if idx > 0:
                    prev_close = df.iloc[idx-1]['Close']
                    print(f"[{name}]   逻辑判断: 成功找到前一交易日 ({dates[idx-1]})，采用其收盘价作为计算基准。")
                else:
                    prev_close = target_row['Open']
                    print(f"[{name}]   ⚠️ 逻辑判断: 数据序列中不存在前一交易日，采用当日开盘价作为计算基准。")

                change_amt = target_row['Close'] - prev_close
                change_pct = (change_amt / prev_close) * 100
                
                hist_data = {
                    'close': target_row['Close'],
                    'prev_close': prev_close,
                    'change_amt': change_amt,
                    'change_pct': change_pct
                }
                
                print(f"[{name}]   🧮 提取与计算结果 (Engine A):")
                print(f"      > 前一基准价 (T-1): {prev_close:,.2f}")
                print(f"      > 目标收盘价 (T):   {hist_data['close']:,.2f}")
                print(f"      > 绝对涨跌额:      {change_amt:+,.2f}")
                print(f"      > 相对涨跌幅:      {change_pct:+.4f}%")
            else:
                print(f"[{name}] 🟡 Engine A (历史库): 目标日期 {target_date} 不在上述拉取序列中 (可能是休市或遭遇雅虎日结延迟)")
        else:
            print(f"[{name}] 🔴 Engine A (历史库): 返回空数据")
    except Exception as e:
        print(f"[{name}] 🔴 Engine A (历史库) 异常: {e}")

    # ==========================================
    # Engine B: 实时快照库 (Snapshot / fast_info)
    # ==========================================
    try:
        fast = ticker.fast_info
        info = ticker.info
        trade_time_utc_ts = info.get('regularMarketTime')
        
        if trade_time_utc_ts:
            trade_dt = datetime.fromtimestamp(trade_time_utc_ts, pytz.utc).astimezone(market_tz)
            trade_date = trade_dt.date()
            
            print(f"\n[{name}] 🔵 Engine B (快照库): 探针发现最新有效交易发生时间:")
            
            # --- 打印时区映射对照 ---
            bjt_tz = pytz.timezone('Asia/Shanghai')
            utc_dt = trade_dt.astimezone(pytz.utc)
            bjt_dt = trade_dt.astimezone(bjt_tz)
            print(f"      📍 当地时间 ({market_info['tz']}): {trade_dt.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
            print(f"      🌐 UTC 时间: {' ' * 14}{utc_dt.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
            print(f"      🇨🇳 北京时间 (BJT): {' ' * 8}{bjt_dt.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
            print(f"      " + "-" * 40)
            
            if trade_date == target_date:
                close_b = fast.last_price
                prev_close_b = fast.previous_close
                change_amt_b = close_b - prev_close_b
                change_pct_b = (change_amt_b / prev_close_b) * 100
                
                snap_data = {
                    'close': close_b,
                    'prev_close': prev_close_b,
                    'change_amt': change_amt_b,
                    'change_pct': change_pct_b
                }
                
                print(f"[{name}]   🧮 提取与计算结果 (Engine B):")
                print(f"      > 快照底层 Previous Close (T-1): {prev_close_b:,.2f}")
                print(f"      > 快照底层 Last Price (T):       {close_b:,.2f}")
                print(f"      > 绝对涨跌额:                   {change_amt_b:+,.2f}")
                print(f"      > 相对涨跌幅:                   {change_pct_b:+.4f}%")
                print(f"[{name}] 🟢 Engine B: 时间戳日期({trade_date})比对吻合，快照有效。")
            elif trade_date < target_date:
                print(f"[{name}] 🟡 Engine B: 最新快照日期({trade_date}) 早于 目标日期({target_date})，证明目标日确未开盘。")
            else:
                print(f"[{name}] 🔴 Engine B: 逻辑异常，快照日期({trade_date}) 晚于 目标日期({target_date})！")
        else:
            print(f"[{name}] 🔴 Engine B (快照库): 无法获取交易时间戳，快照失效。")
    except Exception as e:
        print(f"[{name}] 🔴 Engine B (快照库) 异常: {e}")

    # ==========================================
    # ⚖️ 状态仲裁机 (Arbiter)
    # ==========================================
    print(f"\n[{name}] ⚖️ ------------------- 状态仲裁机 -------------------")
    final_data = None
    status_code = "" # MATCH, FALLBACK, HOLIDAY, MISMATCH, ERROR
    
    if hist_data and snap_data:
        diff = abs(hist_data['close'] - snap_data['close']) / hist_data['close']
        if diff < 0.001:
            print(f"[{name}] 🛡️ 结果: [MATCH] 双库数据一致 (差异极小: {diff*100:.4f}%)。采用历史库数据。")
            final_data = hist_data
            status_code = "MATCH"
        else:
            print(f"[{name}] 🚨 结果: [MISMATCH] 警告！双库数据不一致！历史={hist_data['close']:.2f}, 快照={snap_data['close']:.2f}")
            status_code = "MISMATCH"
            
    elif not hist_data and snap_data:
        print(f"[{name}] 🛡️ 结果: [FALLBACK] 历史库无数据，触发降级保护，采用快照库数据！")
        final_data = snap_data
        status_code = "FALLBACK"
        
    elif hist_data and not snap_data:
        print(f"[{name}] 🛡️ 结果: [HISTORY_ONLY] 快照失效，采用历史库数据。")
        final_data = hist_data
        status_code = "MATCH"
        
    else:
        print(f"[{name}] 🛡️ 结果: [HOLIDAY] 双库均判定无新数据，确认为节假日休市。")
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
    global_mismatch_flag = False 
    
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
        sys.exit(1)

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
