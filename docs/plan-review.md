# ความเห็นต่อแผนฉบับที่ 1 (2 ก.ย. 2026)

สรุป: เห็นด้วยกับแผนเดิมราว 80% ส่วนที่ถูกและยึดไว้คือกฎเหล็ก 7 ข้อ, Build-time AI ≠ Run-time AI, หัวหน้าเป็นโค้ด, และมองแอปเป็น research platform ก่อน สิ่งที่แก้หรือเติมมี 3 กลุ่ม

## 1. รัน Claude 24 ชม. ทำได้ แต่เป็น 3 โหมดตามเฟส

กลไกจริงที่มีในเครื่องนี้

- **Cloud routine** เปิด session ใหม่บนคลาวด์ตาม cron, clone repo จาก GitHub, เปิด PR ได้ ไม่ต้องเปิดเครื่อง แต่มองไม่เห็นไฟล์ในเครื่อง, MT5, journal ความถี่ต่ำสุด 1 ชม. ต้องติดตั้ง Claude GitHub App บน repo ก่อน
- **Scheduled task ในแอป Claude บนเครื่องนี้** รันตาม cron เวลาไทย เห็น journal และ MT5 แต่ทำงานเฉพาะตอนแอปเปิดอยู่

โหมดที่แนะนำ: Phase 0–1 interactive · Phase 2+ cloud routine วันละ 1 รอบสำหรับ backlog ที่ไม่แตะ MT5 · Phase 3+ scheduled task ในเครื่องสำหรับ post-mortem หลังตลาดปิด

สิ่งที่แผนเดิมประเมินต่ำไป: ทุก 6 ชม. เร็วเกินกว่าจะรีวิวทัน และ PR ที่ยังไม่ merge จะมองไม่เห็นกันเอง · agent ที่ไม่มีคนดูรันบนเครื่องเดียวกับเงินจริง ต้องแยก MT5 สองชุด, config live อยู่นอก repo, bridge ปฏิเสธบัญชี live ถ้าไม่มี `ALLOW_LIVE`

ถ้าบอทพังหรือเทรดเสีย: เป็นหน้าที่ของ watchdog, reconnect, kill switch ที่เป็นโค้ด ไม่มีกลไกปลุก Claude ตอนบอทล้ม และไม่ควรมี Claude อ่าน log ตอนกลางคืนแล้วเสนอ PR

## 2. จุดที่เห็นต่างหรือแผนยังขาด

- **ให้ Claude ปรับพารามิเตอร์หลัง post-mortem คือจุดอันตรายที่สุด** แพ้ติดกัน 5 ไม้เป็นเรื่องปกติ ต้องจัดหมวดการขาดทุน (variance / execution / regime / bug) และเปลี่ยนพารามิเตอร์ตามรอบตายตัวพร้อม backtest เต็มประวัติ → D11
- agent "อ่านกราฟ" ถูกตัดโดยไม่ได้บอก เห็นด้วยที่ตัด → D6
- ข้อมูล backtest ยังไม่มีในแผน ต้องมี cache, cost model (spread กว้างตอนข่าว, commission, swap), walk-forward, Monte Carlo → P2-01..03
- SL ที่โบรกมีกับดัก บางโบรกไม่รับ SL ในออเดอร์แรก ต้องยืนยันหลัง fill → smoke.py ทำแล้ว
- Reconcile ทุกนาที ไม่ใช่แค่หลัง crash → P1-08
- Kill switch กดได้จากมือถือผ่าน Telegram `/kill` → P4-02
- ใครสตาร์ท core: background process / task ตอน logon → P1-10
- Secrets ตั้งแต่ commit แรก → `tests/test_no_secrets.py`
- เวลา: UTC ทั้งหมด + offset โบรก → D13, P0-08
- Gate slippage วัดบน demo ไม่ได้ ย้ายไป live_small → D3
- Timeline: เงินจริงเร็วสุดเดือนที่ 6 ไม่ใช่ 4
- DeepSeek: chat สำหรับ Scout/Analyst, reasoner เฉพาะ Reviewer, ไม่ส่งยอดเงินหรือโพซิชันไปกับ prompt

## 3. Flow ที่เสนอเพิ่ม

- Strategy lifecycle เป็น state machine ในแอป → D10, P2-05
- Walk-forward + Monte Carlo ใน backtest engine ตั้งแต่ Phase 2 → P2-03
- Journal browser คือฟีเจอร์ UI ที่มีค่าที่สุด: คลิกไม้แล้วเห็น decision chain 6 ขั้น → wireframe หน้า Journal
- A/B เพิ่มชุดที่สาม (rules + calendar เท่านั้น) → D9

หัวหน้าที่เป็นโค้ด

```python
block     = calendar.block or analyst.block
bias      = analyst.bias                        # -1..1
size_mult = min(analyst.size_mult, regime_cap)
# AI เงียบเกิน 2 ชม. → bias 0, size_mult 1, analyst.block False · calendar.block ยังทำงาน
```
