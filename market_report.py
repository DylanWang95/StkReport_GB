import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta
import pytz
import os
import sys

# --- 1. 基础配置 ---
# 包含美股三大指数 + 欧洲三大指数
MARKETS = {
    'US': [
        {'symbol': '^DJI',  'name': '道指', 'full_name': '美股道指'},
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
    """
    决定我们要抓取哪一天的数据。
    优先级：GitHub手动输入的日期 > 环境变量 > 当前美东时间
    """
    # 1. 检查是否有手动输入的测试日期 (GitHub Actions Input)
    input_date = os.environ.get('INPUT_TEST_DATE')
    if input_date and input_date.strip():
        try:
            target = datetime.strptime(input_date.strip(), "%Y-%m-%d").date()
            print(f"🛠️ [手动测试模式] 正在回溯抓取历史数据: {target}")
            return target
        except ValueError:
            print("⚠️ 输入的日期格式错误，将使用实时时间。")

    # 2. 默认为生产模式：获取美东时间“昨天”的收盘数据
    # (北京时间早晨6:30运行，对应美东时间前一天的下午，交易已结束)
    us_eastern = pytz.timezone('US/Eastern')
    now_us = datetime.now(us_eastern)
    # 如果是周二-周六运行，我们通常看的是"昨天"的收盘
    # 但 yfinance 的逻辑是传入"End Date"，所以我们直接取 .date() 作为基准
    # 比如北京周三早晨(美东周二傍晚)，我们要看周二的数据
    return now_us.date()

def get_market_data(symbol, target_date):
    """
    获取指定日期的指数数据
    """
    try:
        ticker = yf.Ticker(symbol)
        # 宽容度：前后多取几天，防止时区导致的数据缺失
        start_date = target_date - timedelta(days=5)
        end_date = target_date + timedelta(days=2)
        
        df = ticker.history(start=start_date, end=end_date)
        
        if df.empty:
            return None

        # 统一时区处理，只保留日期部分
        try:
            df.index = df.index.tz_convert('US/Eastern').date
        except:
            df.index = df.index.date

        # 检查目标日期是否有数据
        if target_date not in df.index:
            return None # 这一天该市场没开盘（休市）

        # 获取数据
        target_row = df.loc[target_date]
        
        # 寻找前一个有效交易日计算涨跌
        past_data = df.loc[:target_date]
        if len(past_data) < 2:
            prev_close = target_row['Open'] 
        else:
            prev_close = past_data.iloc[-2]['Close']

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
    print(f"🚀 开始生成报表，目标日期: {target_date}")
    
    report_data = [] # 存表格用的详细数据
    
    # --- 1. 处理美股 ---
    us_phrases = []
    us_closed_count = 0
    
    for m in MARKETS['US']:
        data = get_market_data(m['symbol'], target_date)
        if data:
            # 文字：道指收跌1.20%
            text = format_change_text(m['full_name'], data)
            us_phrases.append(text)
            
            # 表格数据
            color = "#ff0000" if data['change_amt'] > 0 else "#008000"
            report_data.append([m['full_name'], f"{data['close']:,.2f}", f"{data['change_amt']:+.2f}", f"{data['change_pct']:+.2f}%", color])
        else:
            us_closed_count += 1
            report_data.append([m['full_name'], "-", "-", "休市", "gray"])

    # 美股文字汇总逻辑
    if us_closed_count == len(MARKETS['US']):
        us_summary = "美股休市"
    else:
        us_summary = "，".join(us_phrases)

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
            # 欧洲如果是个别休市，要明确写出来，例如"英国休市"
            eu_phrases.append(f"{m['country']}休市")
            report_data.append([m['full_name'], "-", "-", "休市", "gray"])

    # 欧股文字汇总逻辑
    if eu_closed_count == len(MARKETS['EU']):
        eu_summary = "欧洲方面休市" # 只有全休市才这么说
    else:
        eu_summary = "欧洲方面，" + "，".join(eu_phrases)

    # --- 3. 全局判断 ---
    total_markets = len(MARKETS['US']) + len(MARKETS['EU'])
    if us_closed_count + eu_closed_count == total_markets:
        print(f"💤 {target_date} 全球主要市场均休市，无需发送邮件。")
        return

    # --- 4. 生成最终文案 ---
    # 格式要求：2026年2月5日 (去掉0)
    date_str = f"{target_date.year}年{target_date.month}月{target_date.day}日"
    
    # 拼接：境外股市运行情况。当地时间X日，[美股部分]。[欧股部分]。
    final_text = f"境外股市运行情况。当地时间{date_str}，{us_summary}。{eu_summary}。"
    
    print("\n生成的文字摘要：")
    print(final_text)

    # --- 5. 发送邮件 ---
    send_email_html(target_date, final_text, report_data)

def send_email_html(date_obj, summary, table_rows):
    sender = os.environ['MAIL_USERNAME'].strip()
    password = os.environ['MAIL_PASSWORD'].strip()
    receiver = os.environ['MAIL_RECEIVER'].strip()
    smtp_server = os.environ['MAIL_SERVER'].strip()
    
    try:
        smtp_port = int(os.environ['MAIL_PORT'])
    except:
        smtp_port = 587

    # 生成HTML行
    html_content = ""
    for row in table_rows:
        # row: [Name, Close, Amt, Pct, Color]
        html_content += f"""
        <tr>
            <td style="padding:8px;border:1px solid #ddd;">{row[0]}</td>
            <td style="padding:8px;border:1px solid #ddd;">{row[1]}</td>
            <td style="padding:8px;border:1px solid #ddd;color:{row[4]}">{row[2]}</td>
            <td style="padding:8px;border:1px solid #ddd;color:{row[4]}">{row[3]}</td>
        </tr>
        """

    email_body = f"""
    <div style="font-family:Arial;color:#333;">
        <h3>🌍 境外股市日报 ({date_obj})</h3>
        <div style="background:#f4f4f4;padding:15px;border-left:5px solid #0366d6;margin-bottom:20px;">
            <strong>📝 文字汇总：</strong><br>{summary}
        </div>
        <table style="border-collapse:collapse;width:100%;text-align:center;font-size:14px;">
            <thead style="background:#0366d6;color:white;">
                <tr>
                    <th style="padding:10px;">指数</th>
                    <th style="padding:10px;">收盘</th>
                    <th style="padding:10px;">涨跌额</th>
                    <th style="padding:10px;">涨跌幅</th>
                </tr>
            </thead>
            <tbody>{html_content}</tbody>
        </table>
    </div>
    """

    msg = MIMEText(email_body, 'html', 'utf-8')
    msg['From'] = formataddr(("股市助手", sender))
    msg['To'] = formataddr(("订阅者", receiver))
    msg['Subject'] = Header(f"【股市日报】{summary[:30]}...", 'utf-8')

    try:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
        server.quit()
        print("✅ 邮件发送成功！")
    except Exception as e:
        print(f"❌ 发送失败: {e}")

if __name__ == "__main__":
    main()
