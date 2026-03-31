import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta, time
import pytz
import os
import pandas as pd

# --- 1. 基础配置 ---
MARKETS = {
    'US': [
        {'symbol': '^DJI',  'name': '道指', 'full_name': '道指', 'tz': 'America/New_York', 'close_hour': 16, 'close_minute': 0},
        {'symbol': '^GSPC', 'name': '标普500', 'full_name': '标普500指数', 'tz': 'America/New_York', 'close_hour': 16, 'close_minute': 0},
        {'symbol': '^IXIC', 'name': '纳指', 'full_name': '纳指', 'tz': 'America/New_York', 'close_hour': 16, 'close_minute': 0}
    ],
    'EU': [
        {'symbol': '^FTSE', 'name': '英国富时', 'full_name': '英国富时100指数', 'country': '英国', 'tz': 'Europe/London', 'close_hour': 16, 'close_minute': 30},
        {'symbol': '^FCHI', 'name': '法国CAC',  'full_name': '法国CAC40指数', 'country': '法国', 'tz': 'Europe/Paris', 'close_hour': 17, 'close_minute': 30},
        {'symbol': '^GDAXI', 'name': '德国DAX',  'full_name': '德国DAX指数', 'country': '德国', 'tz': 'Europe/Berlin', 'close_hour': 17, 'close_minute': 30}
    ]
}

def get_target_date():
    """获取目标日期：优先使用手动输入，否则使用自动逻辑"""
    input_date = os.environ.get('INPUT_TEST_DATE')
    if input_date and input_date.strip():
        try:
            target = datetime.strptime(input_date.strip(), "%Y-%m-%d").date()
            print(f"🛠️ [手动测试模式] 锁定日期: {target}")
            return target
        except ValueError:
            print("⚠️ 日期格式错误，切换回自动模式。")

    us_eastern = pytz.timezone('US/Eastern')
    return datetime.now(us_eastern).date()

def get_market_data(market_info, target_date):
    """获取指定日期的指数数据，包含历史序列打印"""
    symbol = market_info['symbol']
    name = market_info['name']
    
    print(f"\n--- 🔍 开始处理: {name} ({symbol}) ---")
    
    try:
        ticker = yf.Ticker(symbol)
        start_date = target_date - timedelta(days=5)
        end_date = target_date + timedelta(days=3)
        
        print(f"[{name}] 发起API请求，拉取时间窗口: {start_date} 至 {end_date}")
        df = ticker.history(start=start_date, end=end_date)
        
        if df.empty:
            print(f"[{name}] 🚨 逻辑判断: yfinance 返回了空数据。")
            return None

        # 核心逻辑：直接使用本地日期，不进行时区强转
        df.index = [d.date() for d in df.index]

        # ==========================================
        # 新增：打印拉取到的原始历史数据序列
        # ==========================================
        print(f"[{name}] 📊 成功拉取到该窗口内的有效交易日数据:")
        for d, row in df.iterrows():
            marker = " <--- [当前目标日]" if d == target_date else ""
            print(f"   > 日期: {d} | 开盘: {row['Open']:,.2f} | 收盘: {row['Close']:,.2f}{marker}")
        print("-" * 40)
        # ==========================================

        if target_date not in df.index:
            print(f"[{name}] 💤 逻辑判断: 目标日期 {target_date} 不在上述拉取到的有效交易日中，判定为休市。")
            return None 

        target_row = df.loc[target_date]
        all_dates = df.index.tolist()
        
        try:
            idx = all_dates.index(target_date)
            if idx > 0:
                prev_close = df.iloc[idx-1]['Close']
                print(f"[{name}] 逻辑判断: 找到前一交易日 ({all_dates[idx-1]})，采用其收盘价作为基准。")
            else:
                prev_close = target_row['Open']
                print(f"[{name}] ⚠️ 逻辑判断: 数据序列中不存在前一交易日，采用当日开盘价作为基准。")
        except ValueError:
            print(f"[{name}] 🚨 日期索引查找异常。")
            return None

        close = target_row['Close']
        change_amt = close - prev_close
        change_pct = (change_amt / prev_close) * 100
        
        print(f"[{name}] 数据提取与计算结果:")
        print(f"   > 前一基准价 (T-1): {prev_close:,.2f}")
        print(f"   > 目标收盘价 (T):   {close:,.2f}")
        print(f"   > 绝对涨跌额:      {change_amt:+,.2f}")
        print(f"   > 相对涨跌幅:      {change_pct:+.4f}%")
        
        # --- 时区转换与打印逻辑 ---
        local_tz = pytz.timezone(market_info['tz'])
        bjt_tz = pytz.timezone('Asia/Shanghai')
        
        close_time = time(market_info['close_hour'], market_info['close_minute'])
        local_dt_naive = datetime.combine(target_date, close_time)
        
        local_dt_aware = local_tz.localize(local_dt_naive)
        utc_dt = local_dt_aware.astimezone(pytz.utc)
        bjt_dt = local_dt_aware.astimezone(bjt_tz)
        
        print(f"[{name}] 交易日历映射 (基于标准收盘时间):")
        print(f"   📍 当地时间 ({market_info['tz']}): {local_dt_aware.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        print(f"   🌐 UTC 时间: {' ' * 14}{utc_dt.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        print(f"   🇨🇳 北京时间 (BJT): {' ' * 8}{bjt_dt.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        
        return {
            'close': close,
            'change_amt': change_amt,
            'change_pct': change_pct
        }

    except Exception as e:
        print(f"[{name}] 获取失败: {e}")
        return None

def format_change_text(name, data):
    if data['change_amt'] > 0:
        status = "收涨"
    elif data['change_amt'] < 0:
        status = "收跌"
    else:
        status = "收平"
    return f"{name}{status}{abs(data['change_pct']):.2f}%"

def main():
    target_date = get_target_date()
    date_str_cn = f"{target_date.year}年{target_date.month}月{target_date.day}日"
    
    print(f"🚀 开始执行市场报表任务，目标判定日期: {target_date}")
    
    report_data = [] 
    
    # --- 1. 处理美股 ---
    us_phrases = []
    us_closed_count = 0
    for m in MARKETS['US']:
        data = get_market_data(m, target_date)
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
        data = get_market_data(m, target_date)
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

    # --- 3. 全局判断 ---
    if us_closed_count + eu_closed_count == len(MARKETS['US']) + len(MARKETS['EU']):
        print(f"\n💤 结论: {target_date} 全球主要市场均因节假日休市，跳过邮件发送流程。")
        return

    # --- 4. 生成最终文案 ---
    final_text = f"境外股市运行情况。当地时间{date_str_cn}，{us_summary}。{eu_summary}。"
    
    print("\n" + "="*40)
    print("📝 最终生成的文字摘要：")
    print(final_text)
    print("="*40 + "\n")

    subject = f"境外股市运行情况-{date_str_cn}"
    send_email_html(subject, final_text, report_data, date_str_cn)

def send_email_html(subject, summary, table_rows, date_str):
    sender = os.environ.get('MAIL_USERNAME', '').strip()
    password = os.environ.get('MAIL_PASSWORD', '').strip()
    smtp_server = os.environ.get('MAIL_SERVER', '').strip()
    
    if not sender or not password:
        print("⚠️ 邮件发送跳过：未检测到 MAIL_USERNAME 或 MAIL_PASSWORD 环境变量。")
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
