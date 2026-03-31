# ---- 这是对于20260228版本的备份文件
import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta
import pytz
import os
import pandas as pd

# --- 1. 基础配置 ---
MARKETS = {
    'US': [
        {'symbol': '^DJI',  'name': '道指', 'full_name': '道指'},
        {'symbol': '^GSPC', 'name': '标普500', 'full_name': '标普500指数'},
        {'symbol': '^IXIC', 'name': '纳指', 'full_name': '纳指'}
    ],
    'EU': [
        {'symbol': '^FTSE', 'name': '英国富时', 'full_name': '英国富时100指数', 'country': '英国'},
        {'symbol': '^FCHI', 'name': '法国CAC',  'full_name': '法国CAC40指数', 'country': '法国'},
        {'symbol': '^GDAXI', 'name': '德国DAX',  'full_name': '德国DAX指数', 'country': '德国'}
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

    # 自动模式：默认取美东时间"昨天"
    us_eastern = pytz.timezone('US/Eastern')
    return datetime.now(us_eastern).date()

def get_market_data(symbol, target_date):
    """获取指定日期的指数数据"""
    try:
        ticker = yf.Ticker(symbol)
        start_date = target_date - timedelta(days=5)
        end_date = target_date + timedelta(days=3)
        
        df = ticker.history(start=start_date, end=end_date)
        
        if df.empty:
            return None

        # 核心逻辑：直接使用本地日期，不进行时区强转，防止欧股数据丢失
        df.index = [d.date() for d in df.index]

        if target_date not in df.index:
            return None # 这一天该市场没开盘（休市）

        target_row = df.loc[target_date]
        
        # 寻找前一个有效交易日计算涨跌
        all_dates = df.index.tolist()
        try:
            idx = all_dates.index(target_date)
            if idx > 0:
                prev_close = df.iloc[idx-1]['Close']
            else:
                prev_close = target_row['Open']
        except ValueError:
            return None

        close = target_row['Close']
        change_amt = close - prev_close
        change_pct = (change_amt / prev_close) * 100
        
        return {
            'close': close,
            'change_amt': change_amt,
            'change_pct': change_pct
        }

    except Exception as e:
        print(f"获取 {symbol} 失败: {e}")
        return None

def format_change_text(name, data):
    """生成：'道指收跌1.20%' """
    if data['change_amt'] > 0:
        status = "收涨"
    elif data['change_amt'] < 0:
        status = "收跌"
    else:
        status = "收平"
    return f"{name}{status}{abs(data['change_pct']):.2f}%"

def main():
    target_date = get_target_date()
    # 格式化日期字符串：2026年2月5日 (用于标题和正文)
    date_str_cn = f"{target_date.year}年{target_date.month}月{target_date.day}日"
    
    print(f"🚀 开始生成报表，目标日期: {target_date}")
    
    report_data = [] 
    
    # --- 1. 处理美股 ---
    us_phrases = []
    us_closed_count = 0
    
    for m in MARKETS['US']:
        data = get_market_data(m['symbol'], target_date)
        if data:
            text = format_change_text(m['full_name'], data)
            us_phrases.append(text)
            color = "#ff0000" if data['change_amt'] > 0 else "#008000"
            report_data.append([m['full_name'], f"{data['close']:,.2f}", f"{data['change_amt']:+.2f}", f"{data['change_pct']:+.2f}%", color])
        else:
            us_closed_count += 1
            # 表格里显示“因节假日休市”
            report_data.append([m['full_name'], "-", "-", "因节假日休市", "gray"])

    if us_closed_count == len(MARKETS['US']):
        us_summary = "美股因节假日休市"
    else:
        us_summary = "美股" + "，".join(us_phrases)

    # --- 2. 处理欧股 ---
    eu_phrases = []
    eu_closed_count = 0
    
    for m in MARKETS['EU']:
        data = get_market_data(m['symbol'], target_date)
        if data:
            text = format_change_text(m['full_name'], data)
            eu_phrases.append(text)
            color = "#ff0000" if data['change_amt'] > 0 else "#008000"
            report_data.append([m['full_name'], f"{data['close']:,.2f}", f"{data['change_amt']:+.2f}", f"{data['change_pct']:+.2f}%", color])
        else:
            eu_closed_count += 1
            # 文字描述改为：英国因节假日休市
            eu_phrases.append(f"{m['country']}因节假日休市")
            report_data.append([m['full_name'], "-", "-", "因节假日休市", "gray"])

    # 欧股文字汇总逻辑
    if eu_closed_count == len(MARKETS['EU']):
        eu_summary = "欧洲方面因节假日休市"
    else:
        eu_summary = "欧洲方面，" + "，".join(eu_phrases)

    # --- 3. 全局判断 ---
    total_markets = len(MARKETS['US']) + len(MARKETS['EU'])
    if us_closed_count + eu_closed_count == total_markets:
        print(f"💤 {target_date} 全球主要市场均因节假日休市，无需发送邮件。")
        return

    # --- 4. 生成最终文案 ---
    # 格式：境外股市运行情况。当地时间2026年2月5日，美股...。欧洲方面...。
    final_text = f"境外股市运行情况。当地时间{date_str_cn}，{us_summary}。{eu_summary}。"
    
    print("\n生成的文字摘要：")
    print(final_text)

    # 邮件标题：境外股市运行情况-2026年2月5日
    subject = f"境外股市运行情况-{date_str_cn}"
    
    send_email_html(subject, final_text, report_data, date_str_cn)

def send_email_html(subject, summary, table_rows, date_str):
    sender = os.environ['MAIL_USERNAME'].strip()
    password = os.environ['MAIL_PASSWORD'].strip()
    smtp_server = os.environ['MAIL_SERVER'].strip()
    
    # --- 1. 处理多收件人逻辑 ---
    # 获取字符串，按逗号分割，并清理空格
    receivers_str = os.environ['MAIL_RECEIVER']
    receivers = [r.strip() for r in receivers_str.split(',') if r.strip()]
    
    try:
        smtp_port = int(os.environ['MAIL_PORT'])
    except:
        smtp_port = 587

    # --- 2. 邮件正文 (纯文字版) ---
    email_body = f"""
    <div style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #333; line-height: 1.8; font-size: 16px;">
        <p>{summary}</p>
    </div>
    """

    msg = MIMEText(email_body, 'html', 'utf-8')
    
    # 发件人显示名
    msg['From'] = formataddr(("境外股市情况", sender))
    
    # 收件人显示名：为了美观，邮件头里可以只显示“订阅者群组”或者把所有人都列出来
    # 这里我们选择将所有收件人拼接显示，这样大家知道发给了谁
    msg['To'] = ",".join(receivers)
    
    msg['Subject'] = Header(subject, 'utf-8')

    try:
        print(f"正在连接 {smtp_server}:{smtp_port} ...")
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(sender, password)
        
        # --- 3. 这里的 sendmail 必须传入 list (列表) ---
        # 只要 receivers 是一个列表 ['a@a.com', 'b@b.com']，它就会发给所有人
        print(f"正在发送给 {len(receivers)} 位收件人...")
        server.sendmail(sender, receivers, msg.as_string())
        
        server.quit()
        print("✅ 邮件群发成功！")
    except Exception as e:
        print(f"❌ 发送失败: {e}")

if __name__ == "__main__":
    main()
