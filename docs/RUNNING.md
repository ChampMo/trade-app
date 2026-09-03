# การรันจริง — โหมด และการทำให้บอทเดินเองโดยไม่ต้องเปิดหน้าต่าง

## สามโหมด เรียงจากปลอดภัยที่สุด

| โหมด | ราคา | ออเดอร์ | ใช้เมื่อ |
|---|---|---|---|
| `--fake` | จำลองทั้งหมด | จำลอง | ทดสอบว่าโค้ดเดินได้ ไม่ต้องมี MT5 |
| `--paper` | **จริงจาก MT5** | จำลองในเครื่อง ไม่ส่งออกไป | ดูพฤติกรรมของลูปกับตลาดจริงก่อนแตะบัญชี |
| ไม่ใส่ธง | จริง | **ส่งจริง** ไปยังบัญชีของโปรไฟล์ | เทรดจริง |

```bash
python -m tradeapp serve --fake --port 8099      # ปลอดภัยที่สุด
python -m tradeapp serve --paper                 # ราคาจริง ไม่ส่งออเดอร์
python -m tradeapp serve                         # ส่งออเดอร์จริง
```

`serve` จะรันลูปเทรดในเธรดเบื้องหลัง แล้วเปิด API ให้ UI ต่อ ส่วน `run` ทำแค่ลูปอย่างเดียวและพิมพ์ผลออกหน้าจอ

## API

ผูกกับ `127.0.0.1` เท่านั้น และ **ไม่มีระบบยืนยันตัวตน** เพราะออกแบบให้ UI กับ core อยู่เครื่องเดียวกัน ถ้าใส่ host ที่ไม่ใช่ loopback โปรแกรมจะปฏิเสธ ไม่ใช่แค่เตือน เพราะ endpoint `/control/kill` เปิดให้ใครก็ได้กดถ้าหลุดออกไปในเครือข่าย

| Endpoint | ทำอะไร |
|---|---|
| `GET /status` | สถานะ equity peak drawdown จำนวน tick |
| `GET /positions` | โพซิชันที่เปิดอยู่ |
| `GET /events` | เหตุการณ์ ใช้ `after_id` เดินหน้าทีละหน้า |
| `GET /decisions` | ทุกการตัดสินใจ รวมที่ถูกปฏิเสธ พร้อมเหตุผล |
| `GET /orders` | ออเดอร์ พร้อม slippage และธง `sl_verified` |
| `GET /strategies` | กลยุทธ์ พร้อมสถานะ lifecycle |
| `POST /control/kill` | กดเบรก ต้องส่ง `{"reason": "..."}` |
| `POST /control/unlock` | ปลดล็อก **ต้องมีเหตุผล** ไม่งั้น 400 |
| `POST /control/pause` · `/control/resume` | หยุดชั่วคราว และกลับมา |
| `WS /ws/events` | เหตุการณ์สด เริ่มจากปัจจุบัน ไม่ย้อนหลัง |

เปิด `http://127.0.0.1:8001/docs` เพื่อดู schema ทั้งหมด

## ให้บอทเดินเองตอน logon (P1-10)

เป้าหมายคือปิดหน้าต่าง terminal แล้วบอทยังเทรดต่อ ไม่ใช่ต้องเปิดค้างไว้

**1. สร้างสคริปต์เริ่มระบบ** `scripts/start-core.cmd`

```
@echo off
cd /d C:\Users\sones\Documents\GitHub\trade-app
.venv\Scripts\python.exe -m tradeapp serve >> logs\core.log 2>&1
```

**2. ตั้งใน Task Scheduler**

- Trigger: **At log on** ของผู้ใช้คุณ
- Action: Start a program → ชี้ไปที่ `scripts\start-core.cmd`
- ติ๊ก **Run whether user is logged on or not** ไม่ได้ เพราะ MT5 ต้องมี session แบบมีหน้าจอ จึงต้องใช้ at-logon และตั้งเครื่องให้ auto-login
- ติ๊ก **Restart the task if it fails** ทุก 1 นาที ลองใหม่ได้ 3 ครั้ง
- ปิด **Stop the task if it runs longer than** เพราะงานนี้ต้องรันตลอด

**3. สิ่งที่ต้องตั้งในเครื่องด้วย**

- ปิด Sleep และ Hibernate ทั้งหมด (`powercfg /change standby-timeout-ac 0`)
- ตั้ง Windows Update ให้ restart นอกเวลาตลาด
- เปิด MT5 terminal ค้างไว้และล็อกอินอยู่ พร้อมสวิตช์ **Algo Trading** เปิด
- **MT5 ปิดสวิตช์ Algo Trading ทุกครั้งที่เปลี่ยนบัญชี** ต้องกดเปิดใหม่ทุกครั้ง

**4. เช็กว่ามันยังมีชีวิต**

```bash
curl http://127.0.0.1:8001/status
```

ดูที่ `service.ticks` ว่าเพิ่มขึ้นเรื่อย ๆ และ `service.last_error` ว่ายังเป็น null ถ้าลูปตายเพราะ exception มันจะบันทึก CRIT ลง journal แล้วหยุด ไม่ตายเงียบ

Telegram heartbeat จะมาใน P4-02 ซึ่งเป็นวิธีที่ดีกว่าการมานั่งเปิด curl เอง

## งานประจำวัน (post-mortem, scout, reviewer)

สามอย่างนี้ตั้งใน Task Scheduler แบบเดียวกับ start-core.cmd แต่เป็น trigger แบบเวลา ไม่ใช่ at-logon

| เวลา (ไทย) | คำสั่ง | ทำอะไร |
|---|---|---|
| 06:00 | `python -m tradeapp scout --symbol EURUSD` | ถามว่าสัปดาห์นี้มีอะไรน่าสนใจ เก็บเป็น briefing อายุ 36 ชม. ให้ analyst อ่าน |
| 23:30 | `python -m tradeapp report postmortem --write` | สรุปวัน จัดประเภทการขาดทุนด้วยโค้ด ไม่ใช่โมเดล |
| 23:35 | `python -m tradeapp review --write` | ให้ reviewer เขียนความเห็นต่อท้าย post-mortem ของวันนั้น |

ทั้ง scout และ reviewer **ไม่มีอำนาจอะไรเลย** scout เขียนลงปฏิทินไม่ได้ (D24) reviewer เสนอแก้พารามิเตอร์ไม่ได้ (D11)
ถ้าไม่มี `DEEPSEEK_API_KEY` หรืองบรายวันหมด ทั้งคู่จะบอกตรง ๆ แล้วจบ ไม่มีอะไรพัง

ถ้าจะเทียบผลจริงกับ backtest ให้ตั้งเพิ่มสัปดาห์ละครั้ง

```bash
python -m tradeapp report drift --strategy ema_cross --days 7 --write
```

## ถ้าไฟดับหรือเครื่องรีสตาร์ท

โพซิชันที่เปิดอยู่ยังปลอดภัยเพราะ **SL อยู่ที่โบรก** ไม่ได้อยู่ในตัวแปรใน Python (กฎข้อ 3) พอ core กลับมามันจะ reconcile กับโบรกก่อนทำอย่างอื่น และ peak equity กับ day-start equity อยู่ในตาราง `state` ของ journal จึงไม่ถูกล้างตอนรีสตาร์ท (D21)

สิ่งที่ **ไม่** รอด คือรอบเวลาที่พลาดไป ถ้าเครื่องดับข้ามแท่ง H4 ไปสองแท่ง บอทจะไม่ย้อนกลับไปเทรดแท่งที่พลาด มันเริ่มจากแท่งปัจจุบัน ซึ่งเป็นพฤติกรรมที่ตั้งใจ


## ถ้า MT5 หลุด หรือปิด terminal ไปเอง

ตั้งแต่ P4-01 core ซ่อมตัวเองได้: ทุก tick มันถาม terminal ว่ายังต่อกับโบรกอยู่ไหม (ถามที่ `terminal_info().connected`
ไม่ใช่ตัวแปรฝั่งเรา เพราะ MT5 ถือ process ที่หลุดจากโบรกแล้วไว้ได้) ถ้าหลุดมันจะ shutdown แล้วต่อใหม่เอง
พร้อมวัดเวลา server ใหม่ และเขียน WARN ลง journal ทุกครั้ง จำนวนครั้งที่ต่อใหม่โผล่บนหน้า Dashboard ช่อง **Reconnects**

ระหว่างที่ต่อไม่ได้ บอท **ไม่เปิดไม้ใหม่เด็ดขาด** เพราะลิมิตทุกข้อคำนวณจากรายการโพซิชัน การเปิดไม้ทับรายการเก่าที่ไม่รู้ว่าจริงหรือเปล่า
แย่กว่าการพลาดแท่งไปหนึ่งแท่ง ถ้าเงียบเกิน 60 วินาที kill switch จะตัดด้วย trigger `broker_silence` และถ้าตอนนั้นปิดไม้ไม่ได้จริง ๆ
รายงานจะบอกตรง ๆ ว่า **CANNOT REACH THE BROKER** ไม่ใช่ "ปิดครบแล้ว" — เจอข้อความนี้เมื่อไหร่ให้เปิด MT5 ดูด้วยมือทันที

ซ้อมได้ด้วย `python -m tradeapp drill` ซึ่งตอนนี้มี 16 ข้อ แบ่งเป็นชุด kill switch กับชุด watchdog (terminal ตายแล้วกลับมา,
terminal ตายแล้วไม่กลับ, kill ที่ปิดไม่ได้ต้องไม่โกหก, และ bridge ต่อกลับเองได้)

**สิ่งที่ drill ยังพิสูจน์ไม่ได้** คือของจริง: ระหว่าง `serve` เดินอยู่ ให้ปิด MetaTrader 5 ทิ้งไปเลย (หรือ end task `terminal64.exe`)
รอสักครู่แล้วเปิดใหม่ แล้วดูใน journal ว่ามี WARN `reconnected to MT5` และ Dashboard นับ Reconnects เพิ่มขึ้น
ข้อนี้เป็นงานของเจ้าของ และเป็นหนึ่งใน gate ก่อนขึ้นเงินจริง (D3 gate 5)
