# trade-app

Desktop app ควบคุมบอทเทรด MT5 (โบรก XM) · core เป็น Python แยกโปรเซสจาก UI · AI ตอนรันคือ DeepSeek ที่แก้ได้แค่ 3 ค่า · Claude Code ใช้เฉพาะตอนพัฒนา

สถานะ: **Phase 0** · repo ตั้งแล้ว · รอเปิดบัญชี XM demo แล้วรัน smoke ครั้งแรก

เอกสารหลัก

- [CLAUDE.md](CLAUDE.md) กฎเหล็ก สถาปัตยกรรม คำสั่ง และข้อตกลงสำหรับทุก session
- [BACKLOG.md](BACKLOG.md) คิวงานเรียงลำดับ พร้อมเกณฑ์ "เสร็จเมื่อ"
- [docs/DECISIONS.md](docs/DECISIONS.md) การตัดสินใจที่ล็อกแล้ว
- [docs/plan-v1.md](docs/plan-v1.md) แผนฉบับแรก · [docs/plan-review.md](docs/plan-review.md) ความเห็นและจุดที่แก้
- [docs/design/](docs/design/README.md) wireframe โครงสร้าง UX/UI

## เริ่มใช้งาน (Phase 0)

1. เปิดบัญชี **demo** ที่ XM แล้วเปิด MT5 terminal ที่ `C:\Program Files\MetaTrader 5\terminal64.exe` ล็อกอินด้วยบัญชี demo นั้น (ค้นหา server ชื่อ XM ในหน้าล็อกอิน) และเปิดสวิตช์ **Algo Trading** บน toolbar
2. สร้าง `.env` จาก `.env.example` · ต้องมีแค่ `MT5_PATH` ก็รันได้ถ้า terminal ล็อกอินค้างอยู่แล้ว · จะกรอก `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` เพิ่มก็ได้เพื่อล็อกว่าใช้บัญชีไหน (login กับ server ดูได้จาก File > Login to Trade Account ในตัว terminal · password คือรหัสที่โบรกส่งมาตอนเปิดบัญชี ไม่มีแสดงในโปรแกรม) · **ห้ามตั้ง `ALLOW_LIVE`**
3. ติดตั้ง

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,mt5]"
```

4. ตรวจการเชื่อมต่อ ไม่มีการส่งออเดอร์

```bash
python -m tradeapp check
```

ดูว่า Risk Engine จะตัดสินใจอย่างไรกับราคาปัจจุบัน คำสั่งนี้ไม่ส่งออเดอร์

```bash
python -m tradeapp risk --stop-points 200
```

เดินทั้งสายจากแท่งเทียนจริง ผ่านกลยุทธ์ ไปจนถึงคำตัดสินของ Risk Engine โดยไม่ส่งออเดอร์

```bash
python -m tradeapp signals --tf H4
```

เทียบโพซิชันจริงที่โบรกกับที่ journal คิดว่ามี

```bash
python -m tradeapp reconcile
```

ซ้อมกดเบรกฉุกเฉิน ยิงทุก trigger ใส่โบรกจำลอง แล้วบันทึกผลลง journal

```bash
python -m tradeapp drill
```

5. ลอง smoke แบบไม่ต้องมี MT5 ก่อน แล้วค่อยรันจริงบน demo ตอนตลาดเปิด

```bash
python -m tradeapp smoke --fake
python -m tradeapp smoke
python -m tradeapp journal --tail 30
```

เทสและ lint

```bash
pytest -q
ruff check src tests
```

## โครงสร้าง

```
src/tradeapp/
  contracts.py        Intent · OrderRequest · Broker Protocol · Strategy Protocol
  config.py           Settings จาก .env (pydantic-settings)
  broker/             mt5_bridge.py (MT5 จริง) · fake.py (เทส) · guard.py (กัน live)
  journal/            SQLite ผ่าน SQLAlchemy · ทุกการตัดสินใจลงที่นี่
  smoke.py            Phase 0: เปิดปิดออเดอร์ 1 ไม้ พร้อม log ทุกขั้น
tests/                รวม test_no_claude_dependency และ test_no_secrets
docs/                 แผน การตัดสินใจ wireframe
```

คำเตือน: การเทรดด้วยเลเวอเรจมีความเสี่ยงสูญเงินทั้งหมด โค้ดนี้เป็น research platform ไม่ใช่คำแนะนำการลงทุน
