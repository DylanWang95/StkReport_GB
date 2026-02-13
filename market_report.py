import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
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
    # 获取美东时间昨天的日期（因为我们是北京时间早上运行，看的是美国昨天收盘）
    us_eastern = pytz.timezone('US/Eastern')
    now_us = datetime.now(us_eastern)
    
    # 获取最近5天数据，防止周末或假期数据缺失
    end_date = now_us + timedelta(days=1)
    start_date = now_us - timedelta(days=5)
    
    # 期望的交易日是美东时间昨天
    target_date = (now_us - timedelta(days=1)).date()
    print(f"正在获取 {target_date} (美东时间) 的数据...")

    all_closed = True # 假设默认是休市

    for symbol, name in TICKERS.items():
        try:
            ticker = yf.Ticker(symbol)
            # 获取历史数据
            df = ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
            
            if df.empty:
                continue
                
            # 获取最后一行数据
            last_row = df.iloc[-1]
            last_date = last_row.name.date()
            
            # 只有当 最新数据的日期 == 目标日期 时，才说明昨天开市了
            if last_date == target_date:
                all_closed = False # 只要有一个数据对上了，就不是全休市
                
                # 计算数据
                prev_close = df.iloc[-2]['Close'] if len(df) >= 2 else last_row['Open']
                close_price = last_row['Close']
                change_amount = close_price - prev_close
                change_pct = (change_amount / prev_close) * 100
                
                status = "收涨" if change_amount > 0 else "收跌"
                color = "#ff0000" if change_amount > 0 else "#008000" # 红涨绿跌
                
                # 文字描述：道指收跌1.34%
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
    smtp_port = int(os.environ['MAIL_PORT'])

    message = MIMEText(body, 'html', 'utf-8')
    message['From'] = Header("美股日报", 'utf-8')
    message['To'] = Header("订阅者", 'utf-8')
    message['Subject'] = Header(subject, 'utf-8')

    try:
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

def main():
    data, target_date, all_closed = get_market_data()
    
    if all_closed:
        print(f"{target_date} 美股休市，不发送邮件。")
        return # 直接结束程序

    # 构造邮件内容
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
