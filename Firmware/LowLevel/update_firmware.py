import serial
import struct
import time
import binascii
from cobs import cobs
import requests
import zipfile
import io

# Firmware 自動下載抓取
FIRMWARE_URL = "https://github.com/Phonlin/OpenMower/releases/download/latest/firmware.zip"

# 設定 UART 參數 (根據你的 RPi 實際對接 PICO 的 Serial 埠)
SERIAL_PORT = '/dev/ttyAMA0'
BAUD_RATE = 115200
CHUNK_SIZE = 128  # 降低 Chunk size 以增加穩定性

# Packet IDs (與 datatypes.h 一致)
PACK_ID_FW_BEGIN = 0xE1
PACK_ID_FW_CHUNK = 0xE2
PACK_ID_FW_END   = 0xE3
PACK_ID_FW_ACK   = 0xE4
PACK_ID_FW_ABORT = 0xE5

def calc_crc16_ccitt(data):
    """計算 PacketSerial 使用的 CRC16-CCITT (XMODEM)"""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = (crc << 1)
            crc &= 0xFFFF
    return crc

def send_packet(ser, packet_id, payload=b""):
    """封裝 COBS + CRC16 並發送"""
    # 組成原始資料: [ID] + [Payload] + [CRC16(2 bytes, Little Endian)]
    raw_data = struct.pack('B', packet_id) + payload
    
    # 預留 2 bytes 給 CRC
    crc = calc_crc16_ccitt(raw_data)
    
    # 修改最後兩位為 CRC (依據 main.cpp: data_pointer[size-2] = crc & 0xFF; data_pointer[size-1] = (crc >> 8) & 0xFF;)
    final_raw = raw_data + struct.pack('<H', crc)
    
    # COBS 編碼並加上 0x00 作為結束符
    encoded = cobs.encode(final_raw) + b'\x00'
    ser.write(encoded)
    ser.flush()

def wait_for_ack(ser, timeout=2.0):
    """等待 Pico 回傳 ACK"""
    start_time = time.time()
    buffer = b""
    while time.time() - start_time < timeout:
        if ser.in_waiting > 0:
            char = ser.read(1)
            if char == b'\x00':
                if not buffer: continue
                try:
                    # COBS 解碼
                    decoded = cobs.decode(buffer)
                    # 檢查是否為 ACK Packet
                    if decoded[0] == PACK_ID_FW_ACK:
                        status = decoded[1]
                        return True, status
                except Exception as e:
                    print(f"Decode error: {e}")
                buffer = b""
            else:
                buffer += char
    return False, None

def update_firmware(file_path):
    # 1. 讀取檔案
    with open(file_path, 'rb') as f:
        fw_data = f.read()
    
    fw_size = len(fw_data)
    # 計算整份檔案的 CRC32 (為了 Phase 1 最後驗證)
    fw_crc32 = binascii.crc32(fw_data) & 0xFFFFFFFF
    
    print(f"--- 開始更新 ---")
    print(f"檔案: {file_path}")
    print(f"大小: {fw_size} bytes")
    print(f"CRC32: {hex(fw_crc32)}")

    with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1) as ser:
        # 0. 確保 Buffer 清空
        ser.reset_input_buffer()

        # 2. 發送 FW_BEGIN
        # struct: [fw_size(I), fw_crc32(I), chunk_size(H)] -> 4+4+2 = 10 bytes
        begin_payload = struct.pack('<IIH', fw_size, fw_crc32, CHUNK_SIZE)
        send_packet(ser, PACK_ID_FW_BEGIN, begin_payload)
        
        ok, status = wait_for_ack(ser)
        if not ok or status != 0:
            print(f"錯誤: Pico 拒絕更新 (Status: {status})")
            return

        print("Pico 已就緒，開始傳輸 Chunks...")

        # 3. 分段發送 FW_CHUNK
        for offset in range(0, fw_size, CHUNK_SIZE):
            chunk = fw_data[offset : offset + CHUNK_SIZE]
            # struct: [offset(I)] + [data]
            chunk_payload = struct.pack('<I', offset) + chunk
            
            # 重試機制
            for retry in range(3):
                send_packet(ser, PACK_ID_FW_CHUNK, chunk_payload)
                ok, status = wait_for_ack(ser)
                if ok and status == 0:
                    break
                print(f"重傳 Chunk @ {offset} (Retry {retry+1})...")
                time.sleep(0.05) # 重傳前多等一下
            else:
                print("傳輸失敗：多次重試無效。")
                send_packet(ser, PACK_ID_FW_ABORT)
                return
            
            # 進度條
            progress = (offset + len(chunk)) / fw_size * 100
            print(f"\r進度: {progress:.1f}%", end="")

        print("\n傳輸完成，等待 Pico 驗證 CRC...")

        # 4. 發送 FW_END
        send_packet(ser, PACK_ID_FW_END)
        print("Pico 正在驗證檔案，請稍候...")
        ok, status = wait_for_ack(ser, timeout=600.0) # 寫 Flash/驗證可能需要一點時間
        
        if ok and status == 0:
            print("--- 更新成功！Pico 已通過驗證 ---")
        else:
            print(f"--- 更新失敗！Pico 報錯代碼: {status} ---")

# 抓取並解壓縮
def fetch_latest_firmware(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception as e:
        print(f"下載失敗: {e}")
        return False
    
    with zipfile.ZipFile(io.BytesIO(response.content), "r") as zip_ref:
        zip_ref.extractall("FW")
    
    return True

if __name__ == "__main__":
    FW_PATH = 'FW/firmware/0_13_X/firmware.bin'

    # 確保成功下載才更新
    if fetch_latest_firmware(FIRMWARE_URL):
        update_firmware(FW_PATH)
    else:
        print("終止更新流程")
        exit(1)