import yfinance as yf
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta
import pytz
import os
import pandas as pd # 引入pandas处理时间更稳健

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
    """优先级：GitHub手动输入 > 环境变量 > 美东时间昨天"""
    input_date = os.environ.get('INPUT_TEST_DATE')
    if input_date and input_date.strip():
        try:
            target = datetime.strptime(input_date.strip(), "%Y-%m-%d").date()
            print(f"🛠️ [手动测试模式] 锁定日期: {target}")
            return target
        except ValueError:
            print("⚠️ 日期格式错误，切换回自动模式。")

    # 自动模式：默认取美东时间"昨天"（因为北京早晨运行是看昨晚收盘）
    us_eastern = pytz.timezone('US/Eastern')
    return datetime.now(us_eastern).date()

def get_market_data(symbol, target_date):
    """获取指定日期的指数数据（修复时区BUG版）"""
    try:
        ticker = yf.Ticker(symbol)
        # 宽容度：前后多取几天
        start_date = target_date - timedelta(days=5)
        end_date = target_date + timedelta(days=3) # 多取一点防止边界效应
        
        df = ticker.history(start=start_date, end=end_date)
        
        if df.empty:
            # print(f"DEBUG: {symbol} 返回数据为空")
            return None

        # --- 核心修复 ---
        # 不要强转美东时间，而是直接取“本地日期”
        # yfinance 的索引通常是带时区的 Timestamp，或者是 naive 的
        # 我们统一把索引转为单纯的 date 对象 (YYYY-MM-DD)
        df.index = [d.date() for d in df.index]

        # 检查目标日期是否有数据
        if target_date not in df.index:
            # 增加一个详细Debug，方便看看到底抓到了哪几天
            # print(f"DEBUG: {symbol} 未找到 {target_date}。可用日期: {df.index.tolist()}")
            return None # 真的休市

        # 获取数据
        target_row = df.loc[target_date]
        
        # 寻找前一个有效交易日（用于计算涨跌）
        # 这里的切片逻辑要小心，因为index已经是date对象了
        # 我们重新通过 date 来定位
        
        # 找到目标日期在列表中的位置
        all_dates = df.index.tolist()
        try:
            idx = all_dates.index(target_date)
            if idx > 0:
                prev_close = df.iloc[idx-1]['Close']
            else:
                prev_close = target_row['Open'] # 如果是第一天，用开盘价兜底
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
            report_data.append([m['full_name'], "-", "-", "休市", "gray"])

    if us_closed_count == len(MARKETS['US']):
        us_summary = "美股休市"
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
            # 只有当该国确实休市时，才显示“英国休市”
            # 为防止误判，我们这里只记录，不立即加文字，除非是混合情况
            eu_phrases.append(f"{m['country']}休市")
            report_data.append([m['full_name'], "-", "-", "休市", "gray"])

    # 欧股文字汇总逻辑
    # 过滤掉 "XX休市" 的文本，只保留有数据的，除非全部休市
    valid_eu_phrases = [p for p in eu_phrases if "休市" not in p]
    
    if eu_closed_count == len(MARKETS['EU']):
        eu_summary = "欧洲方面休市"
    elif len(valid_eu_phrases) > 0:
        # 混合状态：有开有停。
        # 比如：英国休市，法国涨...
        eu_summary = "欧洲方面，" + "，".join(eu_phrases) # 这里保留"英国休市"这种描述
    else:
         eu_summary = "欧洲方面，" + "，".join(eu_phrases)

    # --- 3. 全局判断 ---
    total_markets = len(MARKETS['US']) + len(MARKETS['EU'])
    if us_closed_count + eu_closed_count == total_markets:
        print(f"💤 {target_date} 全球主要市场均休市，无需发送邮件。")
        return

    # --- 4. 生成最终文案 ---
    date_str = f"{target_date.year}年{target_date.month}月{target_date.day}日"
    final_text = f"境外股市运行情况。当地时间{date_str}，{us_summary}。{eu_summary}。"
    
    print("\n生成的文字摘要：")
    print(final_text)

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
        print(f"正在连接 {smtp_server}:{smtp_port} ...")
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
