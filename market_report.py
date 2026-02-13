import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta
import pytz
import os

# --- 版本验证 ---
print("🚀 代码版本: V3.0 (如果不显示这行，说明代码没更新成功)")

# --- 股票代码配置 ---
TICKERS = {
    '^DJI': '道琼斯指数',
    '^GSPC': '标普500指数',
    '^IXIC': '纳斯达克指数'
}

def get_market_data():
    data_list = []
    us_eastern = pytz.timezone('US/Eastern')
    now_us = datetime.now(us_eastern)
    
    end_date = now_us + timedelta(days=1)
    start_date = now_us - timedelta(days=5)
    
    target_date = (now_us - timedelta(days=1)).date()
    print(f"正在获取 {target_date} (美东时间) 的数据...")

    all_closed = True 

    for symbol, name in TICKERS.items():
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
            
            if df.empty:
                continue
                
            last_row = df.iloc[-1]
            last_date = last_row.name.date()
            
            if last_date == target_date:
                all_closed = False 
                prev_close = df.iloc[-2]['Close'] if len(df) >= 2 else last_row['Open']
                close_price = last_row['Close']
                change_amount = close_price - prev_close
                change_pct = (change_amount / prev_close) * 100
                status = "收涨" if change_amount > 0 else "收跌"
                color = "#ff0000" if change_amount > 0 else "#008000" 
                text_desc = f"{name}{status}{abs(change_pct):.2f}%"
                
                data_list.append({
                    'name': name,
                    'close': f"{close_price:,.2f}",
                    'change_amt': f"{change_amount:+.2f}",
                    'change_pct': f"{change_pct:+.2f}%",
                    'text_desc': text_desc,
                    'color': color
                })
        except Exception as e:
            print(f"获取 {name} 失败: {e}")

    return data_list, target_date, all_closed

def send_email(subject, body):
    # 清理可能存在的空格
    sender = os.environ['MAIL_USERNAME'].strip()
    password = os.environ['MAIL_PASSWORD'].strip()
    receiver = os.environ['MAIL_RECEIVER'].strip()
    smtp_server = os.environ['MAIL_SERVER'].strip()
    
    # 自动补全后缀，防止只填了QQ号
    if '@' not in sender:
        sender = sender + '@qq.com'

    try:
        smtp_port = int(os.environ['MAIL_PORT'])
    except ValueError:
        smtp_port = 465 

    message = MIMEText(body, 'html', 'utf-8')
    
    # --- 核心修复：QQ邮箱必须使用这种格式 ---
    # 格式示例: "美股日报" <123456@qq.com>
    message['From'] = formataddr(("美股日报", sender))
    message['To'] = formataddr(("订阅者", receiver))
    message['Subject'] = Header(subject, 'utf-8')

    print(f"DEBUG: 正在尝试使用的发件人格式: {message['From']}")
    
    try:
        print(f"正在连接 {smtp_server}:{smtp_port} ...")
        
        if smtp_port == 465:
            smtp = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
        else:
            smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
            smtp.starttls()
        
        smtp.login(sender, password)
        smtp.sendmail(sender, receiver, message.as_string())
        print("✅ 邮件发送成功！")
        smtp.quit()
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        # 如果是 550 错误，通常是因为 Header 里的 From 和 Login 的账号不一致
        if "550" in str(
