# DECISIONS — การตัดสินใจที่ล็อกแล้ว

แก้ไฟล์นี้ก่อนแก้โค้ดเสมอ ทุกข้อมีวันที่และเหตุผล ถ้าจะกลับคำต้องเขียนข้อใหม่ ไม่ลบข้อเดิม

| # | วันที่ | การตัดสินใจ | เหตุผล / ผลต่อโค้ด |
|---|---|---|---|
| D1 | 2026-09-02 | โบรก **XM** บน **MT5** เท่านั้น ไม่รองรับ MT4 | MT5 มี Python package ทางการ · MT4 ต้องเขียน EA เชื่อมผ่านไฟล์ซึ่งเปราะ |
| D2 | 2026-09-02 | เริ่มที่ **EURUSD** คู่เดียว | spread ต่ำ พฤติกรรมนิ่ง เหมาะพิสูจน์สถาปัตยกรรมก่อนหา edge |
| D3 | 2026-09-02 | รับ **drawdown สูงสุด 30%** → kill switch ที่ 30% · daily loss limit 3% · risk ต่อไม้เดือนแรกของเงินจริง 0.25% · max open risk 1% · max positions 3 | ตัวเลขนี้เป็น input ของ Risk Engine ไม่ใช่ผลลัพธ์ · gate ก่อนเงินจริง: forward demo ≥ 90 วันไม่แตะพารามิเตอร์ · ≥ 200 ไม้ · DD จริงบน demo ≤ 15% · drill kill switch 3/3 · ผ่านข่าวแรง ≥ 3 ครั้ง · slippage วัดตอน live_small |
| D4 | 2026-09-02 | **ไม่ขาย** แอปให้คนอื่น | ไม่ต้องทำ multi-account, license, แยก config ผู้ใช้ |
| D5 | 2026-09-02 | **Build-time AI ≠ Run-time AI** · Claude Code ใช้พัฒนาเท่านั้น · DeepSeek คือ AI ตอนรัน | บังคับด้วย `tests/test_no_claude_dependency.py` |
| D6 | 2026-09-02 | AI แก้ได้แค่ **bias / size_mult / block** และทุกค่ามีวันหมดอายุ · หัวหน้าเป็นโค้ด ไม่ใช่ LLM · ไม่มี agent อ่านกราฟ | LLM ไม่ deterministic ถ้าให้สั่งเทรดจะ backtest ไม่ได้ · LLM อ่านแท่งเทียนเป็น noise ที่พูดมั่นใจ |
| D7 | 2026-09-02 | UI คุยกับ core ผ่าน **localhost HTTP + WebSocket** ไม่ import กันตรง · core รันเป็น background process โดยไม่ต้องเปิด UI | ปิดหน้าต่างแล้วบอทเทรดต่อ · ย้าย VPS หรือเปลี่ยน shell ได้โดยไม่แตะ logic |
| D8 | 2026-09-02 | **โปรไฟล์การเชื่อมต่อ** = core 1 ตัว + MT5 terminal 1 ชุด + โหมด 1 แบบ · Demo (port 8001, `C:\Program Files\MetaTrader 5`) และ Live (port 8002, ติดตั้งแยกภายหลัง) · สีของ UI เปลี่ยนตามโหมด | แยกเงินจริงออกจากเครื่องมือพัฒนาทางกายภาพ · `ALLOW_LIVE=true` มีเฉพาะบนโปรไฟล์ Live |
| D9 | 2026-09-02 | กลยุทธ์มี **variant A/B/C** (rules · +calendar · +AI) เป็น magic number คนละตัว รันคู่กันบนฟีดเดียว | ตอบได้ด้วยตัวเลขว่า AI ช่วยจริงไหม · ถ้า C ไม่ชนะ B หลัง 3 เดือน ถอด AI |
| D10 | 2026-09-02 | **Strategy lifecycle** research → backtested → forward → live_small → live → retired · gate ระหว่างสถานะเป็นตัวเลขที่แอปตรวจ | วินัยอยู่ในซอฟต์แวร์ ไม่ใช่ในใจตอนตื่นเต้น |
| D11 | 2026-09-02 | Post-mortem ให้ผลลัพธ์เป็น **รายงาน** · ข้อเสนอแก้พารามิเตอร์เป็นข้อยกเว้น ต้องมีหลักฐาน ≥ 30 ไม้ + backtest เต็มประวัติ + walk-forward และเปลี่ยนตามรอบตายตัว (เดือนละครั้ง) · การขาดทุนจัดหมวด variance / execution / regime / bug · มีแค่ execution กับ bug ที่นำไปสู่การแก้โค้ด | กัน overfit กับสัปดาห์ที่แล้ว · แพ้ติดกัน 5 ไม้เป็นเรื่องปกติของ win rate 45% |
| D12 | 2026-09-02 | Kill switch เป็นโค้ดล้วน · ปลดล็อกต้องใส่เหตุผล · หลังปลดล็อกเป็น **PAUSED** ไม่ใช่ RUNNING · Telegram `/kill` ให้ผลเหมือนปุ่ม | ไม่มี AI และไม่มี Claude ในเส้นทางฉุกเฉิน |
| D13 | 2026-09-02 | เวลาใน journal เป็น **UTC** ทั้งหมด (naive UTC ใน SQLite) · เก็บ offset ของ server โบรกไว้ด้วย · UI แสดงเวลาไทยได้แต่ตารางคง UTC | MT5 ใช้เวลา server ไม่ใช่ UTC ไม่ใช่เวลาไทย · วัดจริง 2026-09-02: MetaQuotes-Demo เดิน **UTC+3** โบรกโชว์ 19:47 ขณะที่ UTC จริงคือ 16:47 · ถ้าไม่แปลง ทุกแถวใน journal จะเพี้ยน 3 ชม. เงียบ ๆ · วิธีวัดอยู่ใน `broker/servertime.py` เรียกซ้ำได้หลัง reconnect และช่วงเปลี่ยน DST (ปีละ 2 ครั้ง โบรก EET ขยับระหว่าง +2 กับ +3) |
| D14 | 2026-09-02 | Python 3.13 (package MetaTrader5 มี wheel cp313) · `requires-python >= 3.11` · SQLAlchemy 2 + Pydantic 2 · pydantic-settings อ่าน `.env` | ตรงกับ stack ที่ตกลงกัน |
| D15 | 2026-09-02 | ป้ายใน UI เป็นภาษาอังกฤษ · โค้ดและ commit เป็นอังกฤษ · เอกสารสำหรับเจ้าของเป็นไทยได้ | ศัพท์เทรดเป็นสากล · session อัตโนมัติอ่านอังกฤษได้แม่นกว่า |
| D16 | 2026-09-02 | งานอัตโนมัติของ Claude: Phase 0–1 ทำแบบ interactive · Phase 2+ cloud routine วันละ 1 รอบสำหรับงานที่ไม่แตะ MT5 · Phase 3+ scheduled task บนเครื่องนี้สำหรับ post-mortem · ทุกอย่างเปิด PR เจ้าของ merge | ทุก 6 ชม. เร็วเกินกว่าจะรีวิวทันและ PR จะชนกัน |

## ค่าตั้งต้นที่ยังไม่ล็อก (จะเป็น decision เมื่อพิสูจน์แล้ว)

- ระยะ SL/TP ของ smoke: 20 pips / 40 pips (แค่ให้ผ่าน stops level ของโบรก ไม่ใช่กลยุทธ์)
- หน้าต่าง news block ±30 นาที เฉพาะ HIGH impact
- ชั่วโมงเทรด 07:00–20:00 UTC
- งบ DeepSeek $2/วัน
