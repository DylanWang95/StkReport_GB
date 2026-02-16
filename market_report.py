import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta
import pytz
import os

# --- 1. 定义监控的指数 ---
# 我们把美股和欧股分开定义，方便后面拼凑不同的句子
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

def get_data_and_status(symbol, target_date):
    """
    获取单个指数的数据。
    返回: (状态字符串, 涨跌幅数值, 颜色, 是否休市, 收盘价, 涨跌额)
    """
    try:
        ticker = yf.Ticker(symbol)
        # 多取几天数据，确保能覆盖到目标日期
        # 针对时区差异，我们取过去5天的数据
        end_date = target_date + timedelta(days=2) # 宽容度
        start_date = target_date - timedelta(days=5)
        
        df = ticker.history(start=start_date, end=end_date)
        
        if df.empty:
            return None, 0, "black", True, 0, 0

        # 检查目标日期是否有数据
        # df.index 是 datetime 类型，我们需要转成 date 来比较
        df.index = df.index.tz_convert('US/Eastern').date # 统一转为美东日期比较
        
        if target_date not in df.index:
            return None, 0, "black", True, 0, 0 # 该日无数据，视为休市

        # 获取目标日数据
        target_row = df.loc[target_date]
        
        # 获取前一交易日数据（用于计算涨跌）
        # loc[:target_date] 取目标日及之前的数据，iloc[-2] 取倒数第二行
        past_data = df.loc[:target_date]
        if len(past_data) < 2:
            prev_close = target_row['Open'] # 如果没有前一天，暂用开盘价代替
        else:
            prev_close = past_data.iloc[-2]['Close']

        close_price = target_row['Close']
        change_amount = close_price - prev_close
        change_pct = (change_amount / prev_close) * 100

        # 格式化状态
        if change_amount > 0:
            status_text = "收涨"
            color = "#ff0000" # 红涨
        elif change_amount < 0:
            status_text = "收跌"
            color = "#008000" # 绿跌
        else:
            status_text = "收平"
            color = "black"

        return status_text, change_pct, color, False, close_price, change_amount

    except Exception as e:
        print(f"获取 {symbol} 出错: {e}")
        return None, 0, "black", True, 0, 0

def generate_report():
    # 设定目标日期：美东时间现在
    # 因为脚本在北京时间 06:30 运行，此时是美东时间前一天的 17:30，交易已结束
    # 所以直接取美东时间的 .date() 就是我们要的“交易日”
    us_eastern = pytz.timezone('US/Eastern')
    target_date = datetime.now(us_eastern).date()
    
    print(f"🚀 正在生成 {target_date} 的股市报告...")

    report_data = [] # 存表格数据
    
    # --- 处理美股 ---
    us_texts = []
    us_closed_count = 0
    
    for item in MARKETS['US']:
        status, pct, color, is_closed, close, amt = get_data_and_status(item['symbol'], target_date)
        
        if is_closed:
            us_closed_count += 1
            # 表格里显示休市
            report_data.append({'name': item['full_name'], 'close': '-', 'amt': '-', 'pct': '休市', 'color': 'gray'})
        else:
            # 存入文字描述列表: "道指收跌1.20%"
            us_texts.append(f"{item['full_name']}{status}{abs(pct):.2f}%")
            # 存入表格数据
            report_data.append({
                'name': item['full_name'], 
                'close': f"{close:,.2f}", 
                'amt': f"{amt:+.2f}", 
                'pct': f"{pct:+.2f}%", 
                'color': color
            })

    # 生成美股部分的句子
    if us_closed_count == len(MARKETS['US']):
        us_sentence = "美股休市"
    else:
        us_sentence = "美股" + "，".join(us_texts)


    # --- 处理欧股 ---
    eu_texts = []
    eu_closed_count = 0
    
    for item in MARKETS['EU']:
        status, pct, color, is_closed, close, amt = get_data_and_status(item['symbol'], target_date)
        
        if is_closed:
            eu_closed_count += 1
            # 文字描述: "英国休市"
            eu_texts.append(f"{item['country']}休市")
            report_data.append({'name': item['full_name'], 'close': '-', 'amt': '-', 'pct': '休市', 'color': 'gray'})
        else:
            # 文字描述: "英国富时100指数收跌0.90%"
            eu_texts.append(f"{item['full_name']}{status}{abs(pct):.2f}%")
            report_data.append({
                'name': item['full_name'], 
                'close': f"{close:,.2f}", 
                'amt': f"{amt:+.2f}", 
                'pct': f"{pct:+.2f}%", 
                'color': color
            })
            
    # 生成欧股部分的句子
    eu_sentence = "欧洲方面，" + "，".join(eu_texts)

    # --- 汇总判断 ---
    # 如果美股和欧股全都休市，则不发送
    total_markets = len(MARKETS['US']) + len(MARKETS['EU'])
    if us_closed_count + eu_closed_count == total_markets:
        print("💤 所有市场均休市，不发送邮件。")
        return None, None

    # --- 最终文字拼接 ---
    # 格式：境外股市运行情况。当地时间2026年2月5日，美股...。欧洲方面...。
    final_text = f"境外股市运行情况。当地时间{target_date.strftime('%Y年%m月%d日')}，{us_sentence}。{eu_sentence}。"
    
    return final_text, report_data

def send_email(subject, body):
    sender = os.environ['MAIL_USERNAME'].strip()
    password = os.environ['MAIL_PASSWORD'].strip()
    receiver = os.environ['MAIL_RECEIVER'].strip()
    smtp_server = os.environ['MAIL_SERVER'].strip()
    
    try:
        smtp_port = int(os.environ['MAIL_PORT'])
    except ValueError:
        smtp_port = 587

    message = MIMEText(body, 'html', 'utf-8')
    message['From'] = formataddr(("股市助手", sender))
    message['To'] = formataddr(("订阅者", receiver))
    message['Subject'] = Header(subject, 'utf-8')

    try:
        print(f"正在连接 {smtp_server}:{smtp_port} ...")
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        smtp.starttls()
        smtp.login(sender, password)
        smtp.sendmail(sender, receiver, message.as_string())
        print("✅ 邮件发送成功！")
        smtp.quit()
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

def main():
    summary_text, table_data = generate_report()
    
    if summary_text is None:
        return # 全休市，直接结束

    # 生成 HTML 表格
    html_rows = ""
    for d in table_data:
        html_rows += f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;">{d['name']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{d['close']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; color:{d['color']}">{d['amt']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; color:{d['color']}">{d['pct']}</td>
        </tr>
        """
        
    email_body = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h3 style="color: #333;">🌍 境外股市日报</h3>
        
        <div style="background-color: #f4f4f4; padding: 15px; border-left: 5px solid #0366d6; margin-bottom: 20px;">
            <strong>📝 文字汇总：</strong><br>
            {summary_text}
        </div>

        <table style="border-collapse: collapse; width: 100%; text-align: center; font-size: 14px;">
            <thead style="background-color: #0366d6; color: white;">
                <tr>
                    <th style="padding: 10px; border: 1px solid #ddd;">指数名称</th>
                    <th style="padding: 10px; border: 1px solid #ddd;">收盘点位</th>
                    <th style="padding: 10px; border: 1px solid #ddd;">涨跌额</th>
                    <th style="padding: 10px; border: 1px solid #ddd;">涨跌幅</th>
                </tr>
            </thead>
            <tbody>
                {html_rows}
            </tbody>
        </table>
        <p style="font-size: 12px; color: #888; margin-top: 20px;">
            注：数据来源 Yahoo Finance，时间为当地交易日。
        </p>
    </div>
    """
    
    # 邮件标题直接用前一段文字，防止太长截取前一部分
    send_email(f"【股市日报】{summary_text[:30]}...", email_body)

if __name__ == "__main__":
    main()
