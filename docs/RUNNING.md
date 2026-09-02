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

## ถ้าไฟดับหรือเครื่องรีสตาร์ท

โพซิชันที่เปิดอยู่ยังปลอดภัยเพราะ **SL อยู่ที่โบรก** ไม่ได้อยู่ในตัวแปรใน Python (กฎข้อ 3) พอ core กลับมามันจะ reconcile กับโบรกก่อนทำอย่างอื่น และ peak equity กับ day-start equity อยู่ในตาราง `state` ของ journal จึงไม่ถูกล้างตอนรีสตาร์ท (D21)

สิ่งที่ **ไม่** รอด คือรอบเวลาที่พลาดไป ถ้าเครื่องดับข้ามแท่ง H4 ไปสองแท่ง บอทจะไม่ย้อนกลับไปเทรดแท่งที่พลาด มันเริ่มจากแท่งปัจจุบัน ซึ่งเป็นพฤติกรรมที่ตั้งใจ
