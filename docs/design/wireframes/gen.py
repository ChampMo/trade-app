# -*- coding: utf-8 -*-
"""Generates the trade-app UX wireframe artboards (.dc.html) + canvas.json."""
import json, os

OUT = os.path.dirname(os.path.abspath(__file__))

CSS = """
    body { margin: 0; background: #efede8; }
    .app { width: 1440px; height: 960px; display: flex; flex-direction: column; background: #faf9f6; color: #1d1d1b;
           font-family: 'Mali', 'Segoe UI', Tahoma, sans-serif; font-size: 14px; overflow: hidden; box-sizing: border-box; }
    .app * { box-sizing: border-box; }
    a { color: #1d1d1b; } a:hover { color: #555; }
    .topbar { height: 54px; flex: 0 0 54px; display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 2px solid #1d1d1b; background: #fff; }
    .brand { font-weight: 700; font-size: 16px; letter-spacing: .02em; }
    .sep { width: 2px; height: 26px; background: #1d1d1b; opacity: .2; flex: 0 0 2px; }
    .badge { display: inline-flex; align-items: center; gap: 6px; border: 1.5px solid #1d1d1b; border-radius: 4px; padding: 3px 10px; font-weight: 700; font-size: 13px; background: #fff; white-space: nowrap; }
    .badge.demo { background: #e3ecff; border-color: #2b5fd9; color: #1a3f9e; }
    .badge.live { background: #ffe0dd; border-color: #b3261e; color: #8f1d16; }
    .badge.paper { background: #eeeeea; border-color: #777; color: #555; }
    .pill { display: inline-flex; align-items: center; border: 1.5px solid #1d1d1b; border-radius: 999px; padding: 1px 10px; font-size: 12px; font-weight: 700; white-space: nowrap; background: #fff; line-height: 1.5; }
    .pill.ok { background: #dff3e3; border-color: #1b7f3b; color: #155f2c; }
    .pill.warn { background: #fff1cf; border-color: #8a5d00; color: #6b4800; }
    .pill.bad { background: #ffe0dd; border-color: #b3261e; color: #8f1d16; }
    .pill.muted { background: #eeeeea; border-color: #999; color: #666; }
    .pill.info { background: #e3ecff; border-color: #2b5fd9; color: #1a3f9e; }
    .stat { display: flex; flex-direction: column; gap: 1px; min-width: 80px; }
    .stat .k { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: #666; white-space: nowrap; }
    .stat .v { font-size: 15px; font-weight: 700; white-space: nowrap; line-height: 1.2; }
    .pos { color: #1b7f3b; } .neg { color: #b3261e; } .warn-t { color: #8a5d00; } .muted { color: #777; }
    .kill { margin-left: auto; border: 2px solid #b3261e; color: #b3261e; background: #fff; font-weight: 700; padding: 5px 16px; border-radius: 4px; letter-spacing: .1em; }
    .body { display: flex; flex: 1; min-height: 0; }
    .nav { width: 180px; flex: 0 0 180px; border-right: 2px solid #1d1d1b; background: #fff; display: flex; flex-direction: column; padding: 10px 0; }
    .nav .item { padding: 9px 18px; font-size: 15px; font-weight: 600; }
    .nav .item.active { background: #1d1d1b; color: #fff; }
    .nav .foot { margin-top: auto; padding: 12px 18px; font-size: 11px; color: #666; line-height: 1.5; }
    .main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; padding: 12px 16px 14px; overflow: hidden; }
    .pagehead { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; }
    .pagehead h1 { margin: 0; font-size: 20px; font-weight: 700; line-height: 1.2; }
    .pagehead .sub { color: #666; font-size: 13px; }
    .card { border: 1.5px solid #1d1d1b; border-radius: 4px; background: #fff; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; min-height: 0; min-width: 0; overflow: hidden; }
    .card h3 { margin: 0; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #555; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .card h3 .r { margin-left: auto; text-transform: none; letter-spacing: 0; font-weight: 600; color: #666; }
    .ph { border: 1.5px dashed #8a8a86; background: repeating-linear-gradient(135deg, #f1f0ec 0 8px, #faf9f6 8px 16px); display: flex; align-items: center; justify-content: center; color: #6f6f6b; font-size: 13px; text-align: center; padding: 8px; min-height: 0; flex: 1; }
    .btn { display: inline-flex; align-items: center; justify-content: center; border: 1.5px solid #1d1d1b; border-radius: 4px; padding: 4px 12px; background: #fff; font-size: 13px; font-weight: 700; white-space: nowrap; line-height: 1.5; }
    .btn.primary { background: #1d1d1b; color: #fff; }
    .btn.danger { border-color: #b3261e; color: #b3261e; }
    .btn.off { opacity: .4; border-style: dashed; }
    .btn.sm { padding: 1px 8px; font-size: 12px; }
    .row { display: flex; gap: 12px; align-items: center; min-width: 0; }
    .col { display: flex; flex-direction: column; gap: 8px; min-height: 0; min-width: 0; }
    .grid { display: grid; gap: 12px; min-height: 0; min-width: 0; }
    table.wf { border-collapse: collapse; width: 100%; font-size: 13px; }
    table.wf th { text-align: left; border-bottom: 1.5px solid #1d1d1b; padding: 4px 8px; font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: #555; white-space: nowrap; }
    table.wf td { border-bottom: 1px solid #e2e0da; padding: 4px 7px; white-space: nowrap; vertical-align: middle; }
    table.wf.wrap td { white-space: normal; line-height: 1.35; }
    table.wf tr.sel td { background: #f1efe9; }
    .bar { height: 10px; border: 1.5px solid #1d1d1b; border-radius: 2px; background: #fff; position: relative; flex: 1; min-width: 60px; }
    .bar i { position: absolute; left: 0; top: 0; bottom: 0; background: #1d1d1b; display: block; }
    .bar i.warn { background: #c98a00; } .bar i.bad { background: #b3261e; } .bar i.ok { background: #1b7f3b; }
    .note { font-size: 12px; color: #666; line-height: 1.45; }
    .step { display: flex; align-items: stretch; }
    .step .s { flex: 1; min-width: 0; border: 1.5px solid #1d1d1b; padding: 5px 6px; text-align: center; font-size: 12px; font-weight: 700; background: #fff; margin-left: -1.5px; line-height: 1.3; }
    .step .s:first-child { margin-left: 0; }
    .step .s.cur { background: #1d1d1b; color: #fff; }
    .step .s.done { background: #e6e6e2; }
    .step .s.gate { flex: 0 0 auto; border-style: dashed; font-weight: 500; color: #555; background: #faf9f6; padding: 5px 8px; }
    .toggle { width: 34px; height: 18px; border: 1.5px solid #1d1d1b; border-radius: 999px; position: relative; background: #fff; flex: 0 0 34px; display: inline-block; vertical-align: middle; }
    .toggle i { position: absolute; top: 2px; left: 2px; width: 11px; height: 11px; border-radius: 50%; background: #1d1d1b; display: block; }
    .toggle.on { background: #1d1d1b; } .toggle.on i { left: auto; right: 2px; background: #fff; }
    .field { display: flex; flex-direction: column; gap: 2px; }
    .field .l { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: #666; }
    .field .b { border: 1.5px solid #1d1d1b; border-radius: 4px; padding: 3px 8px; font-size: 13px; background: #fff; display: flex; align-items: center; gap: 6px; white-space: nowrap; overflow: hidden; }
    .field .b .c { margin-left: auto; }
    .tabs { display: flex; gap: 0; border-bottom: 1.5px solid #1d1d1b; }
    .tabs .t { padding: 5px 14px; font-size: 13px; font-weight: 700; border: 1.5px solid #1d1d1b; border-bottom: none; margin-bottom: -1.5px; background: #fff; margin-left: -1.5px; }
    .tabs .t:first-child { margin-left: 0; }
    .tabs .t.on { background: #1d1d1b; color: #fff; }
    .chain { display: flex; flex-direction: column; gap: 0; }
    .chain .c { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; border-bottom: 1px dashed #d5d3cc; font-size: 12.5px; line-height: 1.4; }
    .chain .n { width: 22px; height: 22px; border: 1.5px solid #1d1d1b; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; flex: 0 0 22px; }
    .chain .t { font-weight: 700; }
    .kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px; font-size: 13px; }
    .kv .k { color: #666; }
    .banner { flex: 0 0 auto; background: #b3261e; color: #fff; padding: 8px 16px; display: flex; align-items: center; gap: 14px; font-weight: 700; font-size: 14px; }
    .banner .btn { border-color: #fff; color: #b3261e; background: #fff; }
    .chk { width: 14px; height: 14px; border: 1.5px solid #1d1d1b; border-radius: 3px; display: inline-block; vertical-align: middle; margin-right: 6px; background: #fff; }
    .chk.on { background: #1d1d1b; }
    .list { display: flex; flex-direction: column; gap: 0; }
    .list .li { padding: 6px 8px; border-bottom: 1px solid #e2e0da; display: flex; gap: 10px; align-items: center; font-size: 13px; }
    .list .li.sel { background: #f1efe9; }
    .tick { color: #1b7f3b; font-weight: 700; }
    .x { color: #b3261e; font-weight: 700; }
"""

NAV = ["Dashboard", "Strategies", "Research", "Journal", "AI", "Risk", "Reports", "Events", "Settings"]


def chev():
    return ('<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg>')


def topbar(mode="demo", state="RUNNING", state_cls="ok"):
    label = {"demo": "DEMO · XM #[acct]", "live": "LIVE · XM #[acct]", "paper": "PAPER · XM demo feed"}[mode]
    return f"""<div class="topbar">
  <div class="brand">trade-app</div>
  <div class="badge {mode}">{label} {chev()}</div>
  <div class="pill {state_cls}">{state}</div>
  <div class="sep"></div>
  <div class="stat"><span class="k">Equity</span><span class="v">10,024.60</span></div>
  <div class="stat"><span class="k">Today</span><span class="v pos">+24.60 · +0.25%</span></div>
  <div class="stat" style="min-width: 150px;"><span class="k">Daily loss 0.4% / 3%</span><div class="bar"><i style="width: 13%;"></i></div></div>
  <div class="stat" style="min-width: 150px;"><span class="k">Drawdown 2.1% / 30%</span><div class="bar"><i style="width: 7%;"></i></div></div>
  <div class="sep"></div>
  <div class="stat"><span class="k">UTC · BKK</span><span class="v">14:03 · 21:03</span></div>
  <div class="row" style="gap: 6px;"><span class="pill ok">MT5</span><span class="pill ok">Core</span><span class="pill ok">TG</span></div>
  <div class="kill">KILL</div>
</div>"""


def shell(active, main_html, mode="demo", state=("RUNNING", "ok"), banner=""):
    nav = "".join(f'<div class="item{" active" if n == active else ""}">{n}</div>' for n in NAV)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Mali:wght@400;500;600;700&amp;display=swap">
  <style>{CSS}</style>
</helmet>
<div class="app">
{topbar(mode, *state)}
{banner}
<div class="body">
  <div class="nav">{nav}<div class="foot">core http://127.0.0.1:8001<br>app v0.1 · core v0.1</div></div>
  <div class="main">
{main_html}
  </div>
</div>
</div>
</x-dc>
</body>
</html>
"""


def bare(inner):
    """Artboard without the app shell (diagram pages)."""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Mali:wght@400;500;600;700&amp;display=swap">
  <style>{CSS}</style>
</helmet>
{inner}
</x-dc>
</body>
</html>
"""


def card(title, inner, style="", right=""):
    r = f'<span class="r">{right}</span>' if right else ""
    h = f"<h3>{title}{r}</h3>" if title else ""
    return f'<div class="card" style="{style}">{h}{inner}</div>'


def table(headers, rows, sel=None):
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = []
    for i, r in enumerate(rows):
        cls = ' class="sel"' if sel == i else ""
        trs.append(f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return f'<table class="wf"><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'


def bar(pct, cls="", label=""):
    lab = f'<span style="font-size: 12px; white-space: nowrap; min-width: 92px; text-align: right;">{label}</span>' if label else ""
    return f'<div class="row" style="gap: 8px;"><div class="bar"><i class="{cls}" style="width: {pct}%;"></i></div>{lab}</div>'


def gauge(name, pct, cls, label):
    return f'<div class="col" style="gap: 3px;"><div class="row" style="justify-content: space-between; font-size: 13px;"><span style="font-weight: 700;">{name}</span><span>{label}</span></div><div class="bar"><i class="{cls}" style="width: {pct}%;"></i></div></div>'


def pill(t, c=""):
    return f'<span class="pill {c}">{t}</span>'


def btn(t, c=""):
    return f'<span class="btn {c}">{t}</span>'


def field(label, value, c=True):
    return f'<div class="field"><span class="l">{label}</span><div class="b">{value}{("<span class=\"c\">" + chev() + "</span>") if c else ""}</div></div>'


def kv(pairs):
    return '<div class="kv">' + "".join(f'<span class="k">{k}</span><span>{v}</span>' for k, v in pairs) + "</div>"


def chain(items):
    out = []
    for i, (t, d) in enumerate(items, 1):
        out.append(f'<div class="c"><span class="n">{i}</span><div><span class="t">{t}</span> · {d}</div></div>')
    return '<div class="chain">' + "".join(out) + "</div>"


def head(title, sub="", extra=""):
    s = f'<span class="sub">{sub}</span>' if sub else ""
    return f'<div class="pagehead"><h1>{title}</h1>{s}<div class="row" style="margin-left: auto; gap: 8px;">{extra}</div></div>'


# ---------------------------------------------------------------- Dashboard
def page_dashboard():
    r1 = f"""<div class="grid" style="grid-template-columns: repeat(4, minmax(0, 1fr)); flex: 0 0 118px;">
{card("System", f'<div class="row">{pill("RUNNING","ok")}<span>since 02 Sep 08:00 UTC</span></div><div class="note">heartbeat 14:03 · uptime 6h 03m<br>Telegram ping 14:00</div>')}
{card("MT5 · XM demo", f'<div class="row">{pill("connected","ok")}<span>server 16:03 · ping 42 ms</span></div><div class="note">Algo Trading ON · EURUSD<br>terminal C:\\MT5-demo</div>')}
{card("AI layer", f'<div class="row">{pill("ON","ok")}<span>Analyst 13:00 · Scout 14:00</span></div><div class="note">EURUSD bias −0.3 · size × 0.8 · Risk-off<br>budget $0.42 / $2.00</div>')}
{card("Reconcile", f'<div class="row">{pill("OK","ok")}<span>14:02 UTC</span></div><div class="note">MT5 1 position = journal 1<br>0 mismatches today</div>')}
</div>"""
    equity = card("Equity", '<div class="ph">equity curve · lightweight-charts<br>markers: trades · kill events · param changes</div>',
                  right=f'{btn("Today","sm primary")} {btn("7d","sm")} {btn("30d","sm")}')
    gauges = card("Risk gauges", gauge("Daily loss", 13, "ok", "0.4% / 3.0%") + gauge("Drawdown", 7, "ok", "2.1% / 30%") +
                  gauge("Open risk", 25, "ok", "0.25% / 1.0%") + gauge("Positions", 33, "ok", "1 / 3") +
                  f'<div class="row" style="margin-top: 4px;">{pill("kill switch armed","ok")}<span class="note">triggers on Risk page</span></div>')
    r2 = f'<div class="grid" style="grid-template-columns: 2fr 1fr; flex: 1;">{equity}{gauges}</div>'
    positions = card("Open positions", table(
        ["Strategy", "Symbol", "Side", "Lots", "Entry", "SL", "TP", "PnL", "Age", ""],
        [["trend_h4 · B", "EURUSD", "LONG", "0.05", "1.0868", "1.0842", "1.0910", '<span class="pos">+21.00</span>', "2h 10m", btn("Close", "sm danger")]]),
        right="SL verified at broker for 1 / 1")
    news = card("Next 24h · news blocks", f"""
<div class="row">{pill("BLOCKED 14:00–15:00","bad")}<span class="note">USD Retail Sales · no new intents</span></div>
{table(["UTC", "Event", "Impact", "Block"], [
  ["14:30", "USD Retail Sales", pill("HIGH","bad"), "14:00–15:00"],
  ["18:00", "USD FOMC minutes", pill("HIGH","bad"), "17:30–18:30"],
  ["tmr 12:30", "EUR CPI flash", pill("MED","warn"), "12:00–13:00"],
])}""", right="from calendar · no LLM")
    r3 = f'<div class="grid" style="grid-template-columns: 3fr 2fr; flex: 0 0 236px;">{positions}{news}</div>'
    events = card("Recent events", table(["UTC", "Sev", "Source", "Message"], [
        ["14:03:12", pill("INFO", "muted"), "exec", "order filled #4821 · trend_h4 · B · 0.05 lot · slippage 0.4 pip"],
        ["13:58:41", pill("WARN", "warn"), "mt5", "reconnected after 4 s (attempt 1)"],
        ["11:00:05", pill("WARN", "warn"), "risk", "intent rejected · trend_h4 · C · AI block active"],
    ]), style="flex: 0 0 172px;", right="all events on Events page")
    return shell("Dashboard", head("Dashboard", "ตรวจ 1 นาทีแล้วปิดแอปได้ บอทเทรดต่อเพราะ core แยกโปรเซส") + r1 + r2 + r3 + events)


# ---------------------------------------------------------------- Strategies
def page_strategies():
    tog_on = '<span class="toggle on"><i></i></span>'
    tog_off = '<span class="toggle"><i></i></span>'
    rows = [
        ["trend_h4 · H4", "A · rules", pill("forward", "info"), '<span class="pos">+1.2%</span>', "84", tog_on],
        ["trend_h4 · H4", "B · +calendar", pill("forward", "info"), '<span class="pos">+1.6%</span>', "79", tog_on],
        ["trend_h4 · H4", "C · +AI", pill("forward", "info"), '<span class="pos">+1.1%</span>', "80", tog_on],
        ["meanrev_m15", "A · rules", pill("backtested", "muted"), "—", "—", tog_off],
        ["breakout_h1", "—", pill("research", "muted"), "—", "—", tog_off],
    ]
    left = card("Strategies · EURUSD · 5", table(["Strategy", "Variant", "State", "30d", "Trades", "On"], rows, sel=1) +
                '<div class="note">variant = กลยุทธ์เดียวกัน ต่าง magic number ใช้ทำ A/B บนฟีดเดียวกัน</div>',
                right=btn("+ New", "sm"))
    dh = f"""<div class="row" style="flex: 0 0 auto;"><div class="col" style="gap: 0;"><div class="row" style="gap: 8px;"><span style="font-size: 18px; font-weight: 700;">trend_h4 · B</span>{pill("forward","info")}</div><span class="note">magic 100102 · on Demo core · since 23 Jul</span></div>
<div class="row" style="margin-left: auto; gap: 8px;">{btn("Pause")}{btn("Edit params","off")}{btn("Promote","primary off")}</div></div>"""
    life = card("Lifecycle · selected strategy", f"""<div class="step">
<div class="s done">research</div><div class="s gate">backtest + costs</div><div class="s done">backtested</div><div class="s gate">WF ≥ 0.5 · p95 DD ≤ 15%</div><div class="s cur">forward · day 41 / 90</div><div class="s gate">7 gates</div><div class="s">live_small · 0.25%</div><div class="s gate">1 month ≈ demo</div><div class="s">live</div><div class="s gate">drift</div><div class="s">retired</div></div>""", style="flex: 0 0 auto;")
    gates = card("Gates → live_small", f"""
{gauge("Forward test untouched", 46, "warn", "41 / 90 days")}
{gauge("Trades on demo", 68, "warn", "137 / 200")}
{gauge("Max DD on demo ≤ 15% (half of 30%)", 41, "ok", '6.2% <span class="tick">ok</span>')}
{gauge("Kill-switch drills passed", 100, "ok", '3 / 3 <span class="tick">ok</span>')}
{gauge("High-impact news events survived", 66, "warn", "2 / 3")}
<div class="row" style="font-size: 13px;"><span style="font-weight: 700;">Slippage vs backtest</span><span class="note">measured in live_small only · demo fills are ideal</span></div>
<div class="note">ปุ่ม Promote เปิดเองเมื่อครบทุกข้อ ไม่มีทางลัด</div>""", style="flex: 1;")
    stats = card("30d stats", kv([("PnL", '<span class="pos">+1.6% · +$160.20</span>'), ("Win rate", "44%"), ("Profit factor", "1.41"),
                                  ("Max DD", "6.2%"), ("Avg slippage", "0.5 pip"), ("Expectancy", "$2.03 / trade"), ("Losing streak", "5")]))
    params = card("Params · v3 · read-only", kv([("ema_fast", "20"), ("ema_slow", "50"), ("atr_period", "14"), ("sl_atr_mult", "1.5"), ("rr", "2.0"), ("risk_pct", "0.25")]) +
                  '<div class="note">แก้ค่า = สร้าง v4 และรีเซ็ตนาฬิกา forward เป็น 0 วัน</div>')
    mini = card("30d equity · A vs B vs C", '<div class="ph">three lines · same feed · different magic numbers</div>', style="flex: 1;")
    right = f'<div class="col" style="gap: 12px; min-height: 0;">{dh}<div class="grid" style="grid-template-columns: 1fr 1fr; flex: 1;">{gates}<div class="col" style="gap: 12px;">{stats}{params}{mini}</div></div></div>'
    body = f'<div class="grid" style="grid-template-columns: 500px 1fr; flex: 1;">{left}{right}</div>'
    return shell("Strategies", head("Strategies", "กลยุทธ์เป็นปลั๊กอิน มี lifecycle และ gate ที่แอปบังคับ") + life + body)


# ---------------------------------------------------------------- Research
def page_research():
    cfg = card("Backtest config", f"""
<div class="grid" style="grid-template-columns: 1fr 1fr; gap: 8px;">{field("Strategy", "trend_h4 · v3")}{field("Symbol", "EURUSD")}{field("Timeframe", "H4")}{field("Range", "2021-01 → 2026-08")}</div>
{field("Data", "XM M1 · cached 5.6 y · last sync 01 Sep")}
<h3 style="margin-top: 4px;">Cost model</h3>
<div class="grid" style="grid-template-columns: 1fr 1fr; gap: 8px;">{field("Spread", "tick data · news ×3")}{field("Commission", "$7 / lot RT", False)}{field("Swap", "broker table")}{field("Slippage", "0.3 pip", False)}</div>
<h3 style="margin-top: 4px;">Robustness</h3>
<div class="grid" style="grid-template-columns: 1fr 1fr; gap: 8px;">{field("Walk-forward", "train 6m · test 2m")}{field("Step", "2m", False)}{field("Monte Carlo", "1000 runs · shuffle")}{field("AI replay", "recorded responses")}</div>
<div class="row" style="margin-top: auto; gap: 8px;">{btn("Run backtest", "primary")}{btn("Load run")}<span class="note">~40 s on 5.6 y M1</span></div>""", style="flex: 1;")
    tabs = '<div class="tabs"><div class="t on">Summary</div><div class="t">Walk-forward</div><div class="t">Monte Carlo</div><div class="t">Trades</div><div class="t">Compare runs</div></div>'
    eq = card("Equity + drawdown", '<div class="ph">equity curve on top · drawdown underwater below<br>same on_bar() as live · run #R-0417</div>')
    stats = card("Stats", kv([("Net", '<span class="pos">+18.4%</span>'), ("Trades", "412"), ("Win rate", "44%"), ("Profit factor", "1.38"), ("Sharpe", "1.1"),
                              ("Max DD", "9.8%"), ("Expectancy", "$4.10"), ("Longest losing streak", "7"), ("Avg hold", "31 h"), ("Cost share of gross", "23%")]))
    top = f'<div class="grid" style="grid-template-columns: 2fr 1fr; flex: 1;">{eq}{stats}</div>'
    wf_blocks = "".join('<div class="col" style="gap: 2px; flex: 1;"><div style="border: 1.5px solid #1d1d1b; background: #e6e6e2; height: 14px;"></div><div style="border: 1.5px solid #1d1d1b; background: #1d1d1b; height: 14px;"></div><div class="note" style="text-align: center;">%s</div></div>' % t for t in ["+3.1%", "+1.2%", "−0.8%", "+2.4%", "+1.9%"])
    wf = card("Walk-forward · 5 windows", f'<div class="row" style="gap: 6px;">{wf_blocks}</div><div class="note">grey = train · black = out-of-sample test · efficiency 0.62 <span class="tick">≥ 0.5 ok</span></div>')
    mc = card("Monte Carlo · 1000 runs", '<div class="ph" style="min-height: 60px;">histogram of max drawdown</div><div class="note">p50 DD 8.9% · p95 DD 14.1% <span class="tick">≤ 15% ok</span> · p99 17.6%</div>')
    mid = f'<div class="grid" style="grid-template-columns: 1fr 1fr; flex: 0 0 170px;">{wf}{mc}</div>'
    foot = f'<div class="row" style="flex: 0 0 auto; gap: 8px;"><span class="note">run #R-0417 · 02 Sep 13:41 UTC · params v3 · hash a91f…</span><div class="row" style="margin-left: auto; gap: 8px;">{btn("Save run")}{btn("Promote to forward", "primary")}</div></div><div class="note" style="text-align: right;">Promote ต้องผ่าน WF efficiency ≥ 0.5 และ p95 DD ≤ 15% ไม่งั้นปุ่มเป็นสีเทา</div>'
    right = f'<div class="col" style="gap: 10px;">{tabs}{top}{mid}{foot}</div>'
    body = f'<div class="grid" style="grid-template-columns: 380px 1fr; flex: 1;">{cfg}{right}</div>'
    return shell("Research", head("Research", "backtest ที่คิดต้นทุนจริง แล้วค่อยเลื่อนไป forward") + body)


# ---------------------------------------------------------------- Journal
def page_journal():
    filt = f'<div class="row" style="flex: 0 0 auto; gap: 8px;">{field("Date", "01 Sep → 02 Sep")}{field("Strategy", "all")}{field("Symbol", "EURUSD")}{field("Outcome", "all")}{field("Tag", "any")}<div class="field" style="flex: 1;"><span class="l">Search reason</span><div class="b muted">e.g. pullback</div></div>{btn("Export CSV")}</div>'
    rows = [
        ["14:03:12", "trend_h4 · B", "LONG", pill("APPROVED", "ok"), "1.0868", "0.4p", '<span class="pos">+21.00</span>', pill("open", "info")],
        ["11:00:05", "trend_h4 · C", "LONG", pill("REJECTED · AI block", "bad"), "—", "—", "—", "—"],
        ["09:00:02", "meanrev_m15", "SHORT", pill("REJECTED · netting", "bad"), "—", "—", "—", "—"],
        ["08:00:01", "trend_h4 · A", "LONG", pill("APPROVED", "ok"), "1.0851", "1.1p", '<span class="neg">−18.40</span>', pill("variance", "muted")],
        ["08:00:01", "trend_h4 · B", "LONG", pill("APPROVED", "ok"), "1.0851", "1.0p", '<span class="neg">−14.70</span>', pill("variance", "muted")],
        ["yday 14:30", "trend_h4 · A", "SHORT", pill("APPROVED", "ok"), "1.0899", "2.3p", '<span class="neg">−9.10</span>', pill("execution", "warn")],
        ["yday 12:00", "trend_h4 · C", "—", pill("NO INTENT · block", "muted"), "—", "—", "—", "—"],
    ]
    left = card("Decisions · 01–02 Sep · 7", table(["UTC", "Strategy", "Side", "Risk verdict", "Fill", "Slip", "PnL", "Tag"], rows, sel=0) +
                '<div class="note">แถว = การตัดสินใจ 1 ครั้ง รวมที่ถูก Risk Engine ปฏิเสธ และที่ AI block ไว้ จะได้เห็นว่ากฎแต่ละข้อทำงานจริง</div>')
    detail = card("Decision #4821 · trend_h4 · B · LONG EURUSD", f"""
<div class="ph" style="flex: 0 0 140px;">H4 bars around decision · entry / SL / TP / exit markers</div>
{chain([
  ("Bar + indicators", "H4 close 1.0866 · EMA20 > EMA50 · ATR 0.0042 · RSI 58"),
  ("AI context", 'regime Risk-off · bias −0.3 · size × 0.8 · block no · from Analyst 13:00 · <span style="text-decoration: underline;">raw response</span>'),
  ("Intent", 'LONG · conf 0.70 · SL 1.0842 · TP 1.0910 · "EMA cross + pullback to EMA20"'),
  ("Risk Engine", "APPROVED · 0.05 lot = 0.25% × 0.8 · daily ok · DD ok · netting ok · news ok · SL ok"),
  ("Execution", "filled 1.0868 at 14:03:12 · slippage 0.4 pip · SL verified at broker · retry 0"),
  ("Outcome", 'open · <span class="pos">+21.00</span> · 2h 10m'),
])}
<div class="row" style="margin-top: auto; gap: 6px;"><span style="font-weight: 700; font-size: 12px;">Tag</span>{pill("variance","muted")}{pill("execution","muted")}{pill("regime","muted")}{pill("bug","muted")}<div class="field" style="flex: 1; min-width: 0;"><div class="b muted">note…</div></div></div>""", style="flex: 1;")
    body = f'<div class="grid" style="grid-template-columns: 1fr 460px; flex: 1;">{left}{detail}</div>'
    return shell("Journal", head("Journal", "หัวใจของ research platform ทุกไม้ย้อนดูสายการตัดสินใจได้ครบ") + filt + body)


# ---------------------------------------------------------------- AI
def page_ai():
    ctx = card("Current context · EURUSD", f"""<div class="row" style="gap: 24px;">
<div class="stat"><span class="k">Regime</span><span class="v">Risk-off</span></div>
<div class="stat"><span class="k">Bias −1…1</span><span class="v">−0.3</span></div>
<div class="stat"><span class="k">Size mult 0…1.5</span><span class="v">0.8</span></div>
<div class="stat"><span class="k">Block</span><span class="v">no</span></div>
<div class="stat"><span class="k">Updated</span><span class="v">13:00 UTC</span></div>
<div class="stat"><span class="k">Valid until</span><span class="v">15:00 UTC</span></div>
<div class="note" style="margin-left: auto; max-width: 360px;">3 ค่านี้คือทั้งหมดที่ AI แก้ได้ ถ้าเลยเวลา valid ทุกค่ากลับเป็นกลาง คือ 0 · 1.0 · no · calendar block ยังทำงานเสมอ</div></div>""", style="flex: 0 0 auto;")
    cal = card("Economic calendar · next 48h", table(["UTC", "Cur", "Event", "Impact", "Block window", "Src"], [
        ["14:30", "USD", "Retail Sales", pill("HIGH", "bad"), "14:00–15:00 " + pill("active", "bad"), "cal"],
        ["18:00", "USD", "FOMC minutes", pill("HIGH", "bad"), "17:30–18:30", "cal"],
        ["tmr 12:30", "EUR", "CPI flash", pill("MED", "warn"), "12:00–13:00", "cal"],
        ["tmr 12:30", "USD", "NFP", pill("HIGH", "bad"), "12:00–13:30", "cal"],
    ]) + '<div class="note">block จากปฏิทินไม่ใช้ LLM เลย ทำงานแม้ AI ปิด</div>', right="window −30 / +30 min")
    agents = card("Agents", table(["Agent", "Schedule", "Last run", "Status", ""], [
        ["Scout", "every 15 min", "14:00", pill("ok", "ok"), btn("Run", "sm") + " " + btn("Output", "sm")],
        ["Analyst", "hourly + high-impact", "13:00", pill("ok", "ok"), btn("Run", "sm") + " " + btn("Output", "sm")],
        ["Reviewer", "daily 22:00 UTC", "yday 22:00", pill("ok", "ok"), btn("Run", "sm") + " " + btn("Output", "sm")],
    ]) + '<div class="note">Scout และ Analyst ใช้ deepseek-chat · Reviewer ใช้ reasoner วันละครั้ง · หัวหน้าเป็นโค้ด 3 บรรทัดใน core</div>')
    r2 = f'<div class="grid" style="grid-template-columns: 1fr 1fr; flex: 0 0 auto;">{cal}{agents}</div>'
    budget = card("Budget", gauge("Today", 21, "ok", "$0.42 / $2.00") + gauge("This month", 15, "ok", "$9.10 / $60") +
                  '<div class="note">เกิน cap = AI หลับ ระบบเทรดต่อด้วยกฎ ไม่หยุดทั้งระบบ</div>')
    ab = card("A/B on demo · 30d", table(["Group", "What", "PnL", "Trades", "Max DD"], [
        ["A", "rules only", '<span class="pos">+1.2%</span>', "84", "7.1%"],
        ["B", "rules + calendar block", '<span class="pos">+1.6%</span>', "79", "6.2%"],
        ["C", "rules + calendar + AI", '<span class="pos">+1.1%</span>', "80", "6.8%"],
    ]) + '<div class="note">ถ้า C ไม่ชนะ B หลัง 3 เดือน ถอด AI ออกได้เลย</div>')
    r3 = f'<div class="grid" style="grid-template-columns: 1fr 1fr; flex: 0 0 auto;">{budget}{ab}</div>'
    log = card("Raw call log", table(["UTC", "Agent", "Model", "Tokens", "Cost", "Schema", ""], [
        ["14:00:02", "Scout", "deepseek-chat", "2,110", "$0.001", pill("valid", "ok"), "prompt · response"],
        ["13:00:03", "Analyst", "deepseek-chat", "4,820", "$0.004", pill("valid", "ok"), "prompt · response"],
        ["12:00:04", "Analyst", "deepseek-chat", "4,790", "$0.004", pill("invalid → kept previous", "warn"), "prompt · response"],
    ]), style="flex: 1;", right="ทุก response ดิบถูกเก็บเพื่อ replay ใน backtest")
    extra = f'<span class="note">AI layer</span><span class="toggle on"><i></i></span>{pill("ON","ok")}'
    return shell("AI", head("AI layer · DeepSeek", "อ่านภาษาคน แล้วคืนตัวเลข 3 ค่า", extra) + ctx + r2 + r3 + log)


# ---------------------------------------------------------------- Risk
def page_risk():
    limits = card("Account limits", table(["Limit", "Value", "Note"], [
        ["Max drawdown · kill", "30%", "จากคำตอบคุณ"],
        ["Daily loss limit", "3.0%", "≈ 1/10 ของ DD"],
        ["Risk per trade", "0.25%", "เดือนแรกเงินจริง"],
        ["Max open risk", "1.0%", "ทุกโพซิชันรวม"],
        ["Max positions", "3", "ทุกกลยุทธ์"],
        ["Exposure / currency", "2 units", "netting"],
        ["Trading hours", "07–20 UTC", "นอกนี้ไม่รับ intent"],
        ["News block", "±30 min", "HIGH เท่านั้น"],
    ]).replace('class="wf"', 'class="wf wrap"') + f'<div class="col" style="margin-top: auto; gap: 6px;">{btn("Pause trading to edit")}<span class="note">ระหว่าง RUNNING ทุกช่องเป็น read-only</span></div>', style="flex: 1;")
    trig = card("Kill switch triggers · deterministic", f"""
<div class="list">
<div class="li"><span class="chk on"></span>daily loss ≥ 3.0%</div>
<div class="li"><span class="chk on"></span>drawdown ≥ 30%</div>
<div class="li"><span class="chk on"></span>MT5 disconnected &gt; 60 s</div>
<div class="li"><span class="chk on"></span>consecutive order rejects ≥ 3</div>
<div class="li"><span class="chk on"></span>reconcile mismatch (MT5 ≠ journal)</div>
<div class="li"><span class="chk on"></span>manual · UI button or Telegram /kill</div>
</div>
<div class="note"><b>On trigger:</b> close all → drop pending intents → notify Telegram → wait for manual unlock · ไม่มี AI อยู่ในเส้นทางนี้</div>""")
    drills = card("Drills", f'<div class="row" style="gap: 8px; flex-wrap: wrap;">{btn("Simulate MT5 disconnect","sm")}{btn("Simulate broker reject","sm")}{btn("Simulate daily loss hit","sm")}</div>'
                  '<div class="note">last full drill 28 Aug · 3 / 3 passed · gate ข้อ 5 ต้องผ่านก่อน live_small</div>')
    mid = f'<div class="col" style="gap: 12px;">{trig}{drills}</div>'
    hist = card("Kill history", '<div class="list">'
                '<div class="li"><div><b>28 Aug 10:12</b> · drill: disconnect<br><span class="note">2 positions closed · unlocked 10:20 · reason "drill ok"</span></div></div>'
                '<div class="li"><div><b>21 Aug 15:40</b> · daily loss 3.1%<br><span class="note">1 position closed · unlocked 22 Aug 07:05 · reason "NFP spike, SL too tight → tagged execution"</span></div></div>'
                '</div>')
    changes = card("Change log", '<div class="list">'
                   '<div class="li"><div><b>25 Aug</b> · daily loss 2.0% → 3.0%<br><span class="note">reason "too many pauses on normal days" · while PAUSED</span></div></div>'
                   '<div class="li"><div><b>10 Aug</b> · news window ±15 → ±30 min<br><span class="note">reason "slippage 2.3 pip at 14:30 spike"</span></div></div>'
                   '</div>')
    right = f'<div class="col" style="gap: 12px;">{hist}{changes}</div>'
    body = f'<div class="grid" style="grid-template-columns: 1fr 1fr 360px; flex: 1;">{limits}{mid}{right}</div>'
    extra = pill("LOCKED · trading RUNNING", "warn")
    return shell("Risk", head("Risk", "ทางออกเดียวสู่ตลาด แก้ได้เฉพาะตอน PAUSED และทุกการแก้ลง log", extra) + body)


# ---------------------------------------------------------------- Reports
def page_reports():
    lst = card("Reports", '<div class="list">'
               f'<div class="li sel"><div><b>Daily post-mortem</b> · 01 Sep<br><span class="note">Reviewer + Claude nightly · 22:10 UTC</span></div>{pill("NEW","info")}</div>'
               '<div class="li"><div><b>Daily post-mortem</b> · 31 Aug<br><span class="note">read</span></div></div>'
               '<div class="li"><div><b>Weekly drift</b> · W35<br><span class="note">live vs backtest · read</span></div></div>'
               '<div class="li"><div><b>Daily post-mortem</b> · 30 Aug<br><span class="note">read</span></div></div>'
               '<div class="li"><div><b>Kill-switch drill</b> · 28 Aug<br><span class="note">3 / 3 passed</span></div></div>'
               '</div>', style="flex: 1;")
    per = card("Per strategy", table(["Variant", "Trades", "Net", "Note"], [
        ["A · rules", "2", '<span class="neg">−0.3%</span>', "both SL · normal"],
        ["B · +calendar", "2", '<span class="neg">−0.1%</span>', "1 win 1 loss"],
        ["C · +AI", "2", '<span class="pos">+0.0%</span>', "1 blocked by AI"],
    ]))
    cls = card("Losses classified", kv([("Variance", "2"), ("Execution", "1 · #4815 slippage 2.3 pip"), ("Regime", "0"), ("Bug", "0")]) +
               '<div class="note">มีแค่ execution กับ bug ที่นำไปสู่การแก้โค้ด</div>')
    view = card("Daily post-mortem · Mon 01 Sep 2026", f"""
<div class="note">generated 22:10 UTC · sources: journal 01 Sep · Analyst outputs · Reviewer summary · Claude nightly task</div>
<div style="font-size: 14px; line-height: 1.5;"><b>Summary</b> · 6 trades · 3 win · net −0.4% · nothing outside expected variance · losing streak 2 (max seen 5)</div>
<div class="grid" style="grid-template-columns: 1fr 1fr;">{per}{cls}</div>
<div style="font-size: 13px; line-height: 1.5;"><b>Flagged</b> · #4815 slippage 2.3 pip at 14:30 during Retail Sales spike → news window widened 10 Aug did not cover pre-release drift · <span style="text-decoration: underline;">open in Journal</span></div>
<div style="font-size: 13px; line-height: 1.5;"><b>Analyst accuracy</b> · bias direction right 4 / 6 · 1 block window avoided a loss (C vs A on 14:30)</div>
<div class="card" style="background: #faf9f6; border-style: dashed;"><h3>Proposed changes</h3><div style="font-size: 13px;">none today · เกณฑ์เสนอ: หลักฐาน ≥ 30 ไม้ และ backtest เต็มประวัติผ่าน · เมื่อมีจะแสดง evidence, diff, ลิงก์ PR และสถานะ "รอคุณรีวิว" · ไม่มีวัน auto-apply</div></div>""", style="flex: 1;")
    body = f'<div class="grid" style="grid-template-columns: 360px 1fr; flex: 1;">{lst}{view}</div>'
    return shell("Reports", head("Reports", "post-mortem รายวัน และ drift รายสัปดาห์ อ่าน 5 นาทีตอนเช้า") + body)


# ---------------------------------------------------------------- Events
def page_events():
    filt = f'<div class="row" style="flex: 0 0 auto; gap: 8px;">{field("Severity", "all")}{field("Source", "all")}{field("Date", "today")}<div class="field" style="flex: 1;"><span class="l">Search</span><div class="b muted">message text</div></div>{btn("Export")}</div>'
    rows = [
        ["14:03:12", pill("INFO", "muted"), "exec", "order filled #4821 · trend_h4 · B · 0.05 lot · slippage 0.4 pip · SL verified"],
        ["14:02:00", pill("INFO", "muted"), "reconcile", "OK · MT5 1 = journal 1"],
        ["14:00:00", pill("INFO", "muted"), "core", "heartbeat → Telegram delivered"],
        ["13:58:41", pill("WARN", "warn"), "mt5", "reconnected after 4 s · attempt 1 · no orders in flight"],
        ["13:00:03", pill("INFO", "muted"), "ai", "Analyst ok · 4,820 tokens · $0.004 · schema valid"],
        ["12:30:00", pill("INFO", "muted"), "risk", "news block scheduled 14:00–15:00 · USD Retail Sales HIGH"],
        ["12:00:04", pill("WARN", "warn"), "ai", "Analyst response failed schema · kept previous context"],
        ["11:00:05", pill("WARN", "warn"), "risk", "intent rejected · trend_h4 · C · AI block"],
        ["09:00:02", pill("WARN", "warn"), "risk", "intent rejected · meanrev_m15 · USD exposure 2 / 2"],
        ["yday 15:40", pill("CRIT", "bad"), "kill", "daily loss 3.1% ≥ 3.0% · 1 position closed · Telegram sent · waiting unlock"],
    ]
    left = card("Events · today · 10", table(["UTC", "Sev", "Source", "Message"], rows), style="flex: 1;")
    ch = card("Channels", kv([("Telegram", pill("connected", "ok")), ("Heartbeat", "every 15 min · last 14:00"), ("Commands", "/status · /kill"), ("Chat", "[your chat id]")]) +
              f'<div class="row" style="gap: 8px;">{btn("Send test","sm")}{btn("Send /status","sm")}</div><div class="note">เงียบเกิน 30 นาที = เครื่องหลับหรือเน็ตหลุด คุณรู้ก่อนโบรก</div>')
    rules = card("Alert rules", table(["Severity", "Delivery"], [
        [pill("CRIT", "bad"), "Telegram now · repeat every 5 min until acknowledged"],
        [pill("WARN", "warn"), "Telegram batched hourly"],
        [pill("INFO", "muted"), "log only"],
    ]).replace('class="wf"', 'class="wf wrap"'))
    right = f'<div class="col" style="gap: 12px;">{ch}{rules}</div>'
    body = f'<div class="grid" style="grid-template-columns: 1fr 380px; flex: 1;">{left}{right}</div>'
    return shell("Events", head("Events", "ทุกอย่างที่ระบบทำ ไม่ใช่แค่เทรด") + filt + body)


# ---------------------------------------------------------------- Settings
def page_settings():
    profiles = card("Connection profiles", table(["Profile", "Core URL", "Mode", "MT5 terminal", "Account", "Status", ""], [
        [f'<span class="badge demo">DEMO</span>', "http://127.0.0.1:8001", "demo · broker fills", "C:\\MT5-demo\\terminal64.exe", "XM demo #[acct]", pill("connected", "ok"), btn("Disconnect", "sm")],
        [f'<span class="badge paper">PAPER</span>', "http://127.0.0.1:8001", "demo feed · simulated fills", "same terminal", "—", pill("switch on core", "muted"), btn("Use", "sm")],
        [f'<span class="badge live">LIVE</span>', "http://127.0.0.1:8002", "live", "C:\\MT5-live\\terminal64.exe", "—", pill("not set up", "muted"), btn("Set up…", "sm")],
        [f'<span class="badge live">LIVE · VPS</span>', "http://[vps-ip]:8002", "live", "on VPS", "—", pill("later", "muted"), "—"],
    ]) + '<div class="note">1 โปรไฟล์ = core 1 ตัว + MT5 1 ชุด + โหมด 1 แบบ · สลับโปรไฟล์แล้วสีทั้งแอปเปลี่ยน · LIVE ต้องมี ALLOW_LIVE=1 บนเครื่องที่รัน core และพิมพ์ยืนยัน</div>', style="flex: 0 0 auto;")
    disp = card("Display", kv([("Timezone", "Local BKK · tables keep UTC"), ("Theme", "dark · light"), ("Language", "EN · TH"), ("Number format", "1,234.56")]))
    sec = card("Secrets", kv([("DeepSeek API key", pill("configured", "ok")), ("Telegram bot token", pill("configured", "ok")), ("MT5 password", pill("in core config", "ok")), ("ALLOW_LIVE", pill("not set", "muted"))]) +
               '<div class="note">ทั้งหมดอยู่ใน .env ฝั่ง core · UI ไม่เก็บ ไม่แสดง และไม่มีช่องให้พิมพ์</div>')
    data = card("Data", kv([("Journal", "journal.db · 84 MB"), ("History cache", "EURUSD M1 · 5.6 y"), ("Backtest runs", "17"), ("AI raw responses", "1,204")]) +
                f'<div class="row" style="gap: 8px;">{btn("Open folder","sm")}{btn("Export","sm")}{btn("Sync history","sm")}</div>')
    r2 = f'<div class="grid" style="grid-template-columns: repeat(3, minmax(0, 1fr)); flex: 0 0 auto;">{disp}{sec}{data}</div>'
    auto = card("Automation · Claude", table(["Job", "Where", "Schedule", "Output", "Last"], [
        ["Nightly post-mortem", "this PC · scheduled task", "22:15 BKK", "Report in Reports page", "01 Sep ok"],
        ["Weekly drift report", "this PC · scheduled task", "Sun 09:00 BKK", "Report", "31 Aug ok"],
        ["Backlog dev run", "cloud routine · GitHub", "daily 03:00 BKK", "PR for your review", "02 Sep PR #14"],
    ]) + '<div class="note">งานพวกนี้เป็น build-time เท่านั้น แอปไม่ได้ import หรือเรียก Claude ตอนรัน · test_no_claude_dependency กันไว้</div>', style="flex: 1;")
    about = card("About", '<div class="row" style="gap: 24px; font-size: 13px;"><span>app v0.1</span><span>core v0.1</span><span>Python 3.11</span><span>MetaTrader5 pkg 5.0.x</span><span>Electron</span><span class="note" style="margin-left: auto;">logs folder · diagnostics bundle</span></div>', style="flex: 0 0 auto;")
    return shell("Settings", head("Settings", "โปรไฟล์การเชื่อมต่อ และของที่ตั้งครั้งเดียว") + profiles + r2 + auto + about)


# ---------------------------------------------------------------- Kill flow
def page_killflow():
    flow = '<div class="step" style="flex: 0 0 auto;">' + "".join(
        f'<div class="s{" cur" if i == 1 else ""}">{t}</div>' for i, t in enumerate(
            ["any screen", "KILL", "confirm · type KILL", "close all + stop intents", "Telegram CRIT", "KILLED · wait", "Unlock · reason", "PAUSED", "Resume", "RUNNING"])) + "</div>"
    modal1 = f"""<div class="card" style="width: 420px; gap: 10px;">
<h3>Kill switch</h3>
<div style="font-size: 15px; font-weight: 700;">Close ALL positions on <span class="badge demo">DEMO · XM #[acct]</span> and stop trading?</div>
<div class="list"><div class="li">1 open position will be closed at market</div><div class="li">pending intents dropped · strategies stay loaded</div><div class="li">Telegram CRIT sent · unlock requires a reason</div></div>
{field("Type KILL to confirm", "K I L L", False)}
<div class="row" style="justify-content: flex-end; gap: 8px;">{btn("Cancel")}{btn("KILL","danger")}</div></div>"""
    killed = f"""<div class="col" style="width: 520px; gap: 0; border: 1.5px solid #1d1d1b; background: #faf9f6;">
<div class="topbar" style="height: 44px; flex: 0 0 44px; gap: 10px; padding: 0 10px;"><div class="brand" style="font-size: 14px;">trade-app</div><span class="badge demo" style="font-size: 11px;">DEMO</span>{pill("KILLED","bad")}<div class="kill" style="padding: 3px 10px; font-size: 12px;">KILL</div></div>
<div class="banner">KILLED 14:03 UTC · manual (UI) · 1 position closed · trading stopped · Telegram sent {btn("Unlock…","sm")}</div>
<div style="padding: 12px; display: flex; flex-direction: column; gap: 8px; opacity: .45;">
<div class="grid" style="grid-template-columns: 1fr 1fr 1fr; gap: 8px;"><div class="card"><h3>System</h3>KILLED</div><div class="card"><h3>MT5</h3>connected</div><div class="card"><h3>Positions</h3>0</div></div>
<div class="ph" style="height: 110px;">dashboard greyed · read-only</div></div>
<div class="note" style="padding: 8px 12px; border-top: 1px dashed #999;">ปุ่ม KILL ยังกดซ้ำได้เสมอ · Telegram /kill ให้ผลเดียวกัน · ทุกหน้าเห็นแบนเนอร์นี้</div></div>"""
    modal2 = f"""<div class="card" style="width: 420px; gap: 10px;">
<h3>Unlock trading</h3>
<div class="field"><span class="l">Reason · required · logged</span><div class="b" style="height: 64px; align-items: flex-start; white-space: normal;">e.g. daily loss hit during NFP spike, SL tagged execution, widened news window</div></div>
<div class="list"><div class="li"><span class="chk"></span>I reviewed the cause in Journal</div><div class="li"><span class="chk"></span>MT5 positions reconciled = 0 open</div><div class="li"><span class="chk"></span>Risk limits reviewed · unchanged or change logged</div></div>
<div class="row" style="justify-content: flex-end; gap: 8px;">{btn("Cancel")}{btn("Unlock → PAUSED","primary")}</div>
<div class="note">หลังปลดล็อกระบบเป็น PAUSED ไม่ใช่ RUNNING ต้องกด Resume อีกครั้งด้วยตัวเอง</div></div>"""
    inner = f"""<div class="app" style="height: 500px; padding: 16px 20px; gap: 14px;">
<div class="pagehead"><h1>Kill switch flow</h1><span class="sub">โค้ดล้วน ตัดสินใจในเสี้ยววินาที · คนเป็นคนปลดล็อกเท่านั้น</span></div>
{flow}
<div class="row" style="align-items: flex-start; gap: 24px; flex: 1; min-height: 0;">
<div class="col" style="gap: 6px;"><span class="note" style="font-weight: 700;">1 · confirm</span>{modal1}</div>
<div class="col" style="gap: 6px;"><span class="note" style="font-weight: 700;">2 · killed state · every screen</span>{killed}</div>
<div class="col" style="gap: 6px;"><span class="note" style="font-weight: 700;">3 · unlock</span>{modal2}</div>
</div></div>"""
    return bare(inner)


# ---------------------------------------------------------------- Structure
def page_structure():
    chrome_items = ["Profile switcher · DEMO / LIVE", "System state", "Equity", "Today PnL", "Daily loss bar", "Drawdown bar", "UTC + BKK clock", "MT5 · Core · Telegram", "KILL"]
    chrome = card("Global chrome · every screen", '<div class="row" style="gap: 8px; flex-wrap: wrap;">' + "".join(
        f'<span class="pill{" bad" if t == "KILL" else ""}">{t}</span>' for t in chrome_items) +
        '</div><div class="note">Left nav 9 หน้า · สีของ top bar และ badge เปลี่ยนตามโปรไฟล์ DEMO น้ำเงิน LIVE แดง จะได้ไม่มีวันสับสนว่ากำลังคุยกับเงินจริง</div>', style="flex: 0 0 auto;")

    navspec = [
        ("Dashboard", ["status 4 กล่อง: system · MT5 · AI · reconcile", "equity curve · risk gauges", "open positions · news blocks 24h", "recent events"]),
        ("Strategies", ["list + variants A/B/C", "lifecycle stepper", "gates → next state · ตัวเลขล้วน", "params read-only เมื่อ forward/live"]),
        ("Research", ["backtest config + cost model", "summary · equity · stats", "walk-forward · Monte Carlo", "compare runs · promote to forward"]),
        ("Journal", ["ทุกการตัดสินใจ รวมที่ถูกปฏิเสธ", "detail: chart + decision chain 6 ขั้น", "tag: variance · execution · regime · bug", "export CSV"]),
        ("AI", ["context ปัจจุบัน: regime · bias · size · block", "economic calendar + block windows", "agents: Scout · Analyst · Reviewer", "budget · A/B · raw log"]),
        ("Risk", ["account limits · แก้ได้ตอน PAUSED", "kill triggers · deterministic", "drills · kill history", "change log"]),
        ("Reports", ["daily post-mortem", "weekly drift · live vs backtest", "proposed changes + PR link", "ไม่มี auto-apply"]),
        ("Events", ["ทุก event ของระบบ · severity", "Telegram channel · heartbeat", "alert rules"]),
        ("Settings", ["connection profiles · DEMO / LIVE / VPS", "display · secrets status · data", "automation jobs · Claude", "about"]),
    ]
    cells = "".join(
        f'<div class="card" style="gap: 4px;"><h3>{n}</h3><div class="note" style="color: #1d1d1b;">' + "<br>".join("· " + b for b in bs) + "</div></div>"
        for n, bs in navspec)
    nav = f'<div class="grid" style="grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; flex: 0 0 auto;">{cells}</div>'

    states = card("Global states", f"""
<div class="row" style="gap: 10px;"><span style="font-weight: 700; width: 120px; flex: 0 0 120px;">Profile</span>
<div class="step" style="flex: 1;"><div class="s" style="background: #eeeeea;">PAPER · simulated fills</div><div class="s" style="background: #e3ecff;">DEMO · XM demo</div><div class="s" style="background: #ffe0dd;">LIVE · XM real · ALLOW_LIVE</div></div></div>
<div class="row" style="gap: 10px;"><span style="font-weight: 700; width: 120px; flex: 0 0 120px;">System</span>
<div class="step" style="flex: 1;"><div class="s">STARTING</div><div class="s gate">reconcile ok</div><div class="s cur">RUNNING</div><div class="s gate">pause / resume</div><div class="s">PAUSED</div><div class="s gate">trigger</div><div class="s" style="background: #ffe0dd;">KILLED</div><div class="s gate">unlock + reason</div><div class="s">PAUSED</div></div></div>
<div class="row" style="gap: 10px;"><span style="font-weight: 700; width: 120px; flex: 0 0 120px;">Strategy</span>
<div class="step" style="flex: 1;"><div class="s">research</div><div class="s gate">backtest + costs</div><div class="s">backtested</div><div class="s gate">WF ≥ 0.5 · p95 DD ≤ 15%</div><div class="s">forward · demo</div><div class="s gate">90 d · 200 trades · drills · news</div><div class="s">live_small · 0.25%</div><div class="s gate">1 month ≈ demo</div><div class="s">live</div><div class="s gate">drift</div><div class="s">retired</div></div></div>
<div class="note">RECONNECTING และ DISCONNECTED เป็นสถานะย่อยของ RUNNING · เกิน 60 s กลายเป็น trigger ของ kill switch</div>""", style="flex: 0 0 auto;")

    def flow(title, steps, note):
        s = "".join(f'<div class="s">{x}</div>' for x in steps)
        return f'<div class="card" style="gap: 6px;"><h3>{title}</h3><div class="col" style="gap: 0;"><div class="step" style="flex-direction: column;">{s}</div></div><div class="note">{note}</div></div>'
    flows = f"""<div class="grid" style="grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; flex: 1;">
{flow("Daily 5-minute check", ["open app → Dashboard", "Reports · last night's post-mortem", "Journal · flagged trades only", "close app · core keeps trading"], "งานประจำวันของคุณ ไม่ใช่การนั่งเฝ้า")}
{flow("Research → live", ["Research · backtest + WF + MC", "Promote to forward · Demo core", "90 days untouched · gates fill up", "Promote to live_small · Live core · typed confirm", "1 month ≈ demo → live"], "ทุกการเลื่อนขั้นเป็นปุ่มที่แอปเปิดให้เมื่อตัวเลขครบ")}
{flow("Emergency", ["KILL · any screen or Telegram /kill", "confirm · type KILL", "all closed · intents dropped · CRIT sent", "Unlock · reason · checklist", "PAUSED → Resume"], "ไม่มี AI และไม่มี Claude อยู่ในเส้นทางนี้")}
</div>"""
    inner = f"""<div class="app" style="padding: 16px 20px; gap: 12px;">
<div class="pagehead"><h1>trade-app · โครงสร้าง UX/UI · v0.1</h1><span class="sub">wireframe ระดับโครงสร้าง · ยังไม่ใช่ visual design · Electron shell คุยกับ Python core ผ่าน localhost</span></div>
{chrome}{nav}{states}{flows}</div>"""
    return bare(inner)


PAGES = {
    "Structure.dc.html": page_structure,
    "Main.dc.html": page_dashboard,
    "Strategies.dc.html": page_strategies,
    "Research.dc.html": page_research,
    "Journal.dc.html": page_journal,
    "AI.dc.html": page_ai,
    "Risk.dc.html": page_risk,
    "Reports.dc.html": page_reports,
    "Events.dc.html": page_events,
    "Settings.dc.html": page_settings,
    "KillFlow.dc.html": page_killflow,
}

W, H, GX, GY = 1440, 960, 100, 150
COLS = [0, W + GX, 2 * (W + GX)]
ROWS = [0, H + GY, 2 * (H + GY), 3 * (H + GY), 4 * (H + GY)]

canvas = {
    "artboards": [
        {"file": "Structure.dc.html", "title": "00 Structure", "x": COLS[0], "y": ROWS[0], "w": W, "h": H},
        {"file": "Main.dc.html", "title": "01 Dashboard", "x": COLS[0], "y": ROWS[1], "w": W, "h": H},
        {"file": "Strategies.dc.html", "title": "02 Strategies", "x": COLS[1], "y": ROWS[1], "w": W, "h": H},
        {"file": "Research.dc.html", "title": "03 Research", "x": COLS[2], "y": ROWS[1], "w": W, "h": H},
        {"file": "Journal.dc.html", "title": "04 Journal", "x": COLS[0], "y": ROWS[2], "w": W, "h": H},
        {"file": "AI.dc.html", "title": "05 AI layer", "x": COLS[1], "y": ROWS[2], "w": W, "h": H},
        {"file": "Risk.dc.html", "title": "06 Risk", "x": COLS[2], "y": ROWS[2], "w": W, "h": H},
        {"file": "Reports.dc.html", "title": "07 Reports", "x": COLS[0], "y": ROWS[3], "w": W, "h": H},
        {"file": "Events.dc.html", "title": "08 Events", "x": COLS[1], "y": ROWS[3], "w": W, "h": H},
        {"file": "Settings.dc.html", "title": "09 Settings", "x": COLS[2], "y": ROWS[3], "w": W, "h": H},
        {"file": "KillFlow.dc.html", "title": "10 Kill switch flow", "x": COLS[0], "y": ROWS[4], "w": W, "h": 500},
    ],
    "annotations": [
        {"id": "n-structure", "x": COLS[1], "y": ROWS[0], "w": 320,
         "text": "อ่านหน้านี้ก่อน\n\nโปรไฟล์ = core 1 ตัว + MT5 1 ชุด + โหมด 1 แบบ สลับโปรไฟล์แล้วสีทั้งแอปเปลี่ยน\n\nสิ่งที่ล็อกไว้ตั้งแต่ตอนนี้เพื่อไม่ต้องรื้อ: nav 9 หน้า, KILL บน top bar, lifecycle + gate ในแอป, journal เก็บทุกการตัดสินใจ"},
        {"id": "n-structure-2", "x": COLS[1], "y": ROWS[0] + 260, "w": 320,
         "text": "ภาษาใน UI ใช้อังกฤษเพราะศัพท์เทรดเป็นสากล โน้ตไทยคือคำอธิบายให้เรา ตัวเลขทั้งหมดเป็นตัวอย่าง"},
        {"id": "n-dash", "x": COLS[0] + W + 20, "y": ROWS[1] + 720, "w": 60, "text": ""},
        {"id": "n-strat", "x": COLS[1], "y": ROWS[1] - 130, "w": 420,
         "text": "Strategies: variant A/B/C คือกลยุทธ์เดียวกันคนละ magic number รันคู่กันบนฟีดเดียว ทำให้ตอบได้ว่า AI ช่วยจริงไหม ปุ่ม Promote เป็นสีเทาจนกว่าตัวเลขจะครบ"},
        {"id": "n-research", "x": COLS[2], "y": ROWS[1] - 130, "w": 420,
         "text": "Research: cost model และ walk-forward เป็นส่วนหนึ่งของฟอร์ม ไม่ใช่ตัวเลือกเสริม จะได้ไม่มี backtest ที่ไม่คิดต้นทุนหลุดไปถึง forward"},
        {"id": "n-journal", "x": COLS[0], "y": ROWS[2] - 130, "w": 420,
         "text": "Journal: แถวหนึ่งคือการตัดสินใจหนึ่งครั้ง รวมที่ถูกปฏิเสธและที่ถูก block ด้านขวาคือ decision chain 6 ขั้น ใช้ทำ post-mortem ทั้งคนและ Claude"},
        {"id": "n-ai", "x": COLS[1], "y": ROWS[2] - 130, "w": 420,
         "text": "AI: หน้านี้ตอกย้ำว่า AI แก้ได้แค่ bias / size_mult / block และมีวันหมดอายุ calendar block ไม่ใช้ LLM ทำงานแม้ปิด AI"},
        {"id": "n-risk", "x": COLS[2], "y": ROWS[2] - 130, "w": 420,
         "text": "Risk: ค่าทุกช่อง read-only ตอน RUNNING ต้อง Pause ก่อนแก้ และทุกการแก้ต้องใส่เหตุผลลง change log kill trigger เป็นตัวเลขล้วน"},
        {"id": "n-reports", "x": COLS[0], "y": ROWS[3] - 130, "w": 420,
         "text": "Reports: ผลลัพธ์ปกติของ post-mortem คือรายงาน ไม่ใช่การแก้พารามิเตอร์ ถ้ามีข้อเสนอจะโชว์หลักฐานและลิงก์ PR คุณเป็นคน merge เสมอ"},
        {"id": "n-settings", "x": COLS[2], "y": ROWS[3] - 130, "w": 420,
         "text": "Settings: UI ไม่เก็บ secret ใดๆ แสดงแค่สถานะ LIVE ต้องมี ALLOW_LIVE บนเครื่อง core และพิมพ์ยืนยัน งาน Claude ถูกลิสต์ไว้ให้เห็นว่าเป็น build-time"},
        {"id": "n-kill", "x": COLS[1], "y": ROWS[4], "w": 420,
         "text": "Kill flow: ปลดล็อกแล้วไป PAUSED ไม่ใช่ RUNNING ต้องกด Resume เอง Telegram /kill ให้ผลเหมือนปุ่ม"},
    ],
    "launch": {"view": "canvas"},
}
# drop the empty helper note
canvas["annotations"] = [a for a in canvas["annotations"] if a["text"]]

if __name__ == "__main__":
    for name, fn in PAGES.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(fn())
    with open(os.path.join(OUT, "canvas.json"), "w", encoding="utf-8") as f:
        json.dump(canvas, f, ensure_ascii=False, indent=2)
    print("wrote", len(PAGES), "artboards + canvas.json")
