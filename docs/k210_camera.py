# K210 摄像头固件 — MaixPy (MicroPython) 脚本
# 功能: 人脸检测 + 手势识别 + UART JSON 协议与 Pi 5 通信
# 硬件: Sipeed Maix Dock/Go + OV2640 + 2.4" LCD
# 烧录: 保存为 /sd/boot.py 或通过 MaixPy IDE 上传为 main.py

import sensor
import image
import lcd
import KPU as kpu
from machine import UART
from Maix import GPIO, FPIOA
import time
import json
import gc

# ===== 配置 =====
UART_NUM = 1           # UART1: pin 6(RX)/7(TX) on Maix Dock
UART_BAUD = 115200
FACE_CONFIDENCE = 0.7  # 人脸检测阈值
TRACK_TIMEOUT = 30     # 帧数, 超时未检测到视为离开
GESTURE_THRESH = 40    # 像素位移阈值, 判定手势
FRAME_SKIP = 2         # 跳帧: 每 N 帧检测一次 (性能优化)
LCD_ROTATION = 2       # LCD 旋转方向

# ===== 全局状态 =====
tracked_faces = {}     # {face_id: {"bbox": (x,y,w,h), "last_seen": frame, "first_seen": frame}}
next_face_id = 0
face_positions = {}    # {face_id: [(x_center, y_center), ...]}  轨迹历史
frame_count = 0
last_event_time = 0
person_present = False
uart_buffer = ""

# ===== 初始化 =====

def init_camera():
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)  # 320x240
    sensor.set_windowing((224, 224))   # KPU 输入尺寸
    sensor.set_vflip(True)
    sensor.set_hmirror(True)
    sensor.run(1)
    sensor.skip_frames(30)

def init_lcd():
    lcd.init(freq=15000000)
    lcd.rotation(LCD_ROTATION)
    lcd.clear(lcd.WHITE)
    lcd.draw_string(10, 10, "K210 Camera Ready", lcd.BLACK, lcd.WHITE)

def init_uart():
    fpioa = FPIOA()
    # Maix Dock: UART1 TX=pin7, RX=pin6
    fpioa.set_function(6, FPIOA.UART1_RX)
    fpioa.set_function(7, FPIOA.UART1_TX)
    uart = UART(UART_NUM, UART_BAUD)
    uart.write(b'{"event":"boot","msg":"K210 ready"}\n')
    return uart

def load_model():
    try:
        # YOLOv2 face detection model — 需烧录到 flash 0x300000
        # kflash -p /dev/ttyUSB0 -b 1500000 -t face_model.kfpkg
        task = kpu.load(0x300000)
        kpu.set_outputs(task, 0, 1, 0, 1)
        return task
    except Exception as e:
        # 备选: 从 SD 卡加载
        try:
            task = kpu.load("/sd/face_model.kmodel")
            kpu.set_outputs(task, 0, 1, 0, 1)
            return task
        except Exception:
            return None

def init_kpu():
    task = load_model()
    if task is None:
        lcd.draw_string(10, 30, "WARN: no face model", lcd.RED, lcd.WHITE)
    else:
        lcd.draw_string(10, 30, "Face model loaded", lcd.GREEN, lcd.WHITE)
    return task

# ===== 人脸匹配 (IoU) =====

def iou(a, b):
    """计算两个 bbox 的 IoU: a=(x,y,w,h), b=(x,y,w,h)"""
    ax1, ay1 = a[0], a[1]
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx1, by1 = b[0], b[1]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    if inter_x1 >= inter_x2 or inter_y1 >= inter_y2:
        return 0.0
    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    union_area = a[2] * a[3] + b[2] * b[3] - inter_area
    return inter_area / union_area if union_area > 0 else 0.0

def match_face(bbox, threshold=0.3):
    """返回最近似的人脸 ID, 无匹配返回 None"""
    best_id, best_iou = None, threshold
    for fid, info in tracked_faces.items():
        score = iou(bbox, info["bbox"])
        if score > best_iou:
            best_iou = score
            best_id = fid
    return best_id

# ===== 手势检测 =====

def detect_gesture(face_id, x_center, y_center):
    """简单手势: 基于人脸水平位移方向"""
    positions = face_positions.get(face_id, [])
    positions.append((x_center, y_center))
    if len(positions) > 15:
        positions.pop(0)
    face_positions[face_id] = positions
    if len(positions) < 5:
        return None
    dx = positions[-1][0] - positions[0][0]
    dy = positions[-1][1] - positions[0][1]
    if abs(dx) > GESTURE_THRESH and abs(dx) > abs(dy):
        return "wave_right" if dx > 0 else "wave_left"
    if abs(dy) > GESTURE_THRESH and abs(dy) > abs(dx):
        return "wave_down" if dy > 0 else "wave_up"
    return None

# ===== UART 通信 =====

def read_commands(uart):
    """非阻塞读取 UART 指令"""
    global uart_buffer
    if uart.any():
        try:
            chunk = uart.read(uart.any()).decode("utf-8", errors="replace")
            uart_buffer += chunk
            lines = uart_buffer.split("\n")
            uart_buffer = lines[-1]
            for line in lines[:-1]:
                line = line.strip()
                if line.startswith("DISP:"):
                    return ("disp", line[5:])
                elif line.startswith("CMD:"):
                    return ("cmd", line[4:])
                elif line.strip():
                    return ("raw", line)
        except Exception:
            pass
    return None

def send_event(uart, event):
    """发送 JSON 事件到 Pi 5"""
    try:
        payload = json.dumps(event)
        uart.write((payload + "\n").encode("utf-8"))
    except Exception:
        pass

def handle_disp(params, img):
    """处理 Pi 5 发来的 LCD 显示指令: DISP:text:color"""
    try:
        parts = params.split(":")
        text = parts[0] if len(parts) > 0 else ""
        color_hex = int(parts[1], 16) if len(parts) > 1 else 0xFFFF
        # 清除底部区域
        img.draw_rectangle(0, 200, 224, 24, fill=True, color=(255, 255, 255))
        img.draw_string(5, 202, text, color=(color_hex >> 11, (color_hex >> 5) & 0x3F, color_hex & 0x1F), scale=2)
    except Exception:
        pass

# ===== 主循环 =====

def main():
    global tracked_faces, next_face_id, frame_count, person_present, last_event_time

    init_camera()
    init_lcd()
    uart = init_uart()
    kpu_task = init_kpu()

    lcd.draw_string(10, 50, "UART: %d baud" % UART_BAUD, lcd.BLACK, lcd.WHITE)
    lcd.draw_string(10, 70, "Waiting for faces...", lcd.BLUE, lcd.WHITE)
    time.sleep(2)
    lcd.clear(lcd.WHITE)

    anchor = (0.1075, 0.1268, 0.1268, 0.1755, 0.1755, 0.2605, 0.2605, 0.3735, 0.3735)

    while True:
        frame_count += 1
        img = sensor.snapshot()

        # 跳帧: 降低 CPU 负载
        if frame_count % FRAME_SKIP != 0:
            # 仍然处理命令和渲染
            _process_commands(uart, img)
            lcd.display(img)
            continue

        gc.collect()

        # ---- KPU 人脸检测 ----
        faces = []
        if kpu_task:
            try:
                objects = kpu.run_yolo2(kpu_task, img, FACE_CONFIDENCE, anchor, (7, 7, 3))
                if objects:
                    for obj in objects:
                        faces.append((
                            obj.x(), obj.y(), obj.w(), obj.h(),
                            obj.value()  # confidence
                        ))
            except Exception:
                pass

        # ---- 人脸追踪 & 事件生成 ----
        now_ms = time.ticks_ms()
        matched_ids = set()

        for face in faces:
            bbox = face[:4]
            conf = face[4]
            x_center = bbox[0] + bbox[2] // 2
            y_center = bbox[1] + bbox[3] // 2

            fid = match_face(bbox)
            if fid is None:
                # 新人脸
                fid = next_face_id
                next_face_id += 1
                tracked_faces[fid] = {
                    "bbox": bbox,
                    "last_seen": frame_count,
                    "first_seen": frame_count,
                    "confidence": conf,
                }
                face_positions[fid] = [(x_center, y_center)]
                # 发送 enter 事件
                send_event(uart, {
                    "event": "enter",
                    "person": f"person_{fid}",
                    "bbox": list(bbox),
                    "timestamp": now_ms,
                })
                last_event_time = now_ms
            else:
                # 已知人脸: 更新位置
                tracked_faces[fid]["bbox"] = bbox
                tracked_faces[fid]["last_seen"] = frame_count
                tracked_faces[fid]["confidence"] = conf
                matched_ids.add(fid)
                # 手势检测
                gesture = detect_gesture(fid, x_center, y_center)
                if gesture:
                    send_event(uart, {
                        "event": "gesture",
                        "action": gesture,
                        "person": f"person_{fid}",
                        "timestamp": now_ms,
                    })
                    last_event_time = now_ms

            # 绘制 bbox
            img.draw_rectangle(bbox[0], bbox[1], bbox[2], bbox[3],
                               color=(0, 255, 0), thickness=2)
            img.draw_string(bbox[0] + 2, bbox[1] - 16,
                            "ID:%d %.0f%%" % (fid, conf * 100),
                            color=(0, 255, 0), scale=1)

        # ---- 离开检测 ----
        remove_ids = []
        for fid, info in tracked_faces.items():
            if fid not in matched_ids:
                frames_missing = frame_count - info["last_seen"]
                if frames_missing > TRACK_TIMEOUT:
                    remove_ids.append(fid)

        for fid in remove_ids:
            send_event(uart, {
                "event": "leave",
                "person": f"person_{fid}",
                "timestamp": now_ms,
            })
            last_event_time = now_ms
            tracked_faces.pop(fid, None)
            face_positions.pop(fid, None)

        # ---- 人脸数量事件 (变化时发送) ----
        new_count = len(tracked_faces)
        was_present = person_present
        person_present = new_count > 0
        if new_count != was_present or frame_count % 30 == 0:
            send_event(uart, {
                "event": "face_count",
                "count": new_count,
                "timestamp": now_ms,
            })

        # ---- 处理 Pi 5 发来的命令 ----
        _process_commands(uart, img)

        # ---- 状态栏 ----
        img.draw_rectangle(0, 0, 224, 20, fill=True, color=(0, 0, 0))
        img.draw_string(2, 2, "Faces:%d FPS:%d" % (new_count, min(30, frame_count // max(1, (now_ms // 1000)))),
                        color=(255, 255, 255), scale=1)

        # ---- 渲染到 LCD ----
        lcd.display(img)

    # 清理 (实际不会执行到)
    if kpu_task:
        kpu.deinit(kpu_task)


def _process_commands(uart, img):
    """处理来自 Pi 5 的指令"""
    cmd = read_commands(uart)
    if cmd is None:
        return
    cmd_type, params = cmd
    if cmd_type == "disp":
        handle_disp(params, img)
    elif cmd_type == "cmd":
        # 可扩展: 切换模式、拍照等
        if params == "snapshot":
            # 拍照并发送 (大图片走 base64 或 SD 存储)
            pass
    elif cmd_type == "raw":
        # 透传原始指令
        pass


# ===== 启动 =====
if __name__ == "__main__":
    main()
