import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr  # <--- 新增这行，专门解决 550 错误
from datetime import datetime, timedelta
import pytz
import os

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
    
    # 目标日期：美东时间昨天
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
    sender = os.environ['MAIL_USERNAME']
    password = os.environ['MAIL_PASSWORD']
    receiver = os.environ['MAIL_RECEIVER']
    smtp_server = os.environ['MAIL_SERVER']
    
    # 强制转换端口为整数
    try:
        smtp_port = int(os.environ['MAIL_PORT'])
    except ValueError:
        smtp_port = 465 # 默认值

    message = MIMEText(body, 'html', 'utf-8')
    
    # --- 关键修改开始 ---
    # 使用 formataddr 生成标准格式: "美股日报 <xxx@qq.com>"
    # 这能完美解决 QQ 邮箱 550 Error
    message['From'] = formataddr(["美股日报", sender])
    message['To'] = formataddr(["订阅者", receiver])
    # --- 关键修改结束 ---
    
    message['Subject'] = Header(subject, 'utf-8')

    try:
        print(f"正在连接 {smtp_server}:{smtp_port} ...")
        
        if smtp_port == 465:
            smtp = smtplib.SMTP_SSL(smtp_server, smtp_port)
        else:
            smtp = smtplib.SMTP(smtp_server, smtp_port)
            smtp.starttls()
        
        smtp.login(sender, password)
        smtp.sendmail(sender, receiver, message.as_string())
        print("邮件发送成功！")
        smtp.quit()
    except Exception as e:
        print(f"邮件发送失败: {e}")
        # 再次提示用户检查配置
        if "550" in str(e):
             print("如果是 550 错误，通常是因为发送频率过快或发件人格式问题（已在此代码中修复格式）。")

def main():
    data, target_date, all_closed = get_market_data()
    
    if all_closed:
        print(f"{target_date} 美股休市，不发送邮件。")
        return 

    summary_text = f"当地时间{target_date}，美股" + "，".join([d['text_desc'] for d in data]) + "。"
    
    html_rows = ""
    for d in data:
        html_rows += f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;">{d['name']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{d['close']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; color:{d['color']}">{d['change_amt']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; color:{d['color']}">{d['change_pct']}</td>
        </tr>
        """
        
    email_body = f"""
    <h3>🇺🇸 美股收盘速递 ({target_date})</h3>
    <table style="border-collapse: collapse; width: 100%; text-align: center; font-family: Arial;">
        <thead style="background-color: #f2f2f2;">
            <tr>
                <th style="padding: 8px; border: 1px solid #ddd;">指数</th>
                <th style="padding: 8px; border: 1px solid #ddd;">收盘</th>
                <th style="padding: 8px; border: 1px solid #ddd;">涨跌额</th>
                <th style="padding: 8px; border: 1px solid #ddd;">涨跌幅</th>
            </tr>
        </thead>
        <tbody>
            {html_rows}
        </tbody>
    </table>
    <br>
    <div style="background-color: #f9f9f9; padding: 10px; border-left: 4px solid #0366d6;">
        <strong>📝 汇总：</strong>{summary_text}
    </div>
    """
    
    subject = f"【美股日报】{target_date} " + " ".join([d['text_desc'] for d in data])
    send_email(subject, email_body)

if __name__ == "__main__":
    main()
