import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta
import pytz
import os
import time
import requests # 新增：用于自定义网络会话

# --- 1. 基础配置 ---
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
    """获取目标日期"""
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

def get_market_data(symbol, target_date):
    """获取指定日期的指数数据（带自定义超时与 Plan B 抢救机制）"""
    
    # 建立一个自定义的请求会话，伪装成浏览器，并设置防卡死
    session = requests.Session()
    session.headers['User-agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

    for attempt in range(3):
        try:
            # 传入自定义 session，利用 requests 底层机制防止被 GitHub 强杀
            ticker = yf.Ticker(symbol, session=session)
            
            start_date = target_date - timedelta(days=5)
            end_date = target_date + timedelta(days=3)
            
            # --- Plan A: 获取历史账本 ---
            df = ticker.history(start=start_date, end=end_date)
            
            if df.empty:
                print(f"⚠️ [{attempt+1}/3] {symbol} 返回为空，可能是接口限制，2秒后重试...")
                time.sleep(2)
                continue

            df.index = [d.date() for d in df.index]

            if target_date not in df.index:
                # ==========================================
                # --- Plan B: 历史无数据，启动 fast_info 抢救 ---
                # ==========================================
                print(f"🔍 {symbol} 历史账本无 {target_date}，启动 Plan B 尝试实时抓取...")
                try:
                    fast_data = ticker.fast_info
                    latest_price = fast_data.get('lastPrice')
                    prev_close_plan_b = fast_data.get('previousClose')
                    
                    if latest_price and prev_close_plan_b:
                        change_amt = latest_price - prev_close_plan_b
                        change_pct = (change_amt / prev_close_plan_b) * 100
                        print(f"✅ {symbol} Plan B 抢救成功！")
                        return {
                            'close': latest_price,
                            'change_amt': change_amt,
                            'change_pct': change_pct
                        }
                except Exception as b_e:
                    print(f"⚠️ Plan B 获取失败: {b_e}")
                
                # 如果 Plan B 也拿不到，判定为真休市
                print(f"💡 {symbol} 确认为休市。")
                return None 

            # --- Plan A 正常逻辑 ---
            target_row = df.loc[target_date]
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
            # 捕获包括超时在内的所有异常，休眠后重试
            print(f"❌ [{attempt+1}/3] 获取 {symbol} 发生异常: {e}")
            time.sleep(2) 

    print(f"🚨 {symbol} 连续 3 次获取失败，请检查网络或更换数据源。")
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
            eu_phrases.append(f"{m['country']}因节假日休市")
            report_data.append([m['full_name'], "-", "-", "因节假日休市", "gray"])

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
    final_text = f"境外股市运行情况。当地时间{date_str_cn}，{us_summary}。{eu_summary}。"
    print("\n生成的文字摘要：")
    print(final_text)

    subject = f"境外股市运行情况-{date_str_cn}"
    send_email_html(subject, final_text, report_data, date_str_cn)

def send_email_html(subject, summary, table_rows, date_str):
    sender = os.environ['MAIL_USERNAME'].strip()
    password = os.environ['MAIL_PASSWORD'].strip()
    smtp_server = os.environ['MAIL_SERVER'].strip()
    
    receivers_str = os.environ['MAIL_RECEIVER']
    receivers = [r.strip() for r in receivers_str.split(',') if r.strip()]
    
    try:
        smtp_port = int(os.environ['MAIL_PORT'])
    except:
        smtp_port = 587

    html_content = ""
    for row in table_rows:
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
        <h3>🌍 境外股市日报 ({date_str})</h3>
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
    msg['From'] = formataddr(("境外股市情况", sender))
    msg['To'] = ",".join(receivers)
    msg['Subject'] = Header(subject, 'utf-8')

    try:
        print(f"正在连接 {smtp_server}:{smtp_port} ...")
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()
        server.login(sender, password)
        print(f"正在发送给 {len(receivers)} 位收件人...")
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("✅ 邮件群发成功！")
    except Exception as e:
        print(f"❌ 发送失败: {e}")

if __name__ == "__main__":
    main()
