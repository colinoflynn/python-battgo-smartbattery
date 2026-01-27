import serial
import time

from pybattgo import BattGoDecoder, BattGoPhyDecoder

bgphy = BattGoPhyDecoder()
bgdec = BattGoDecoder()

log = open("datalog.txt", "w")
old_cellv = None

with serial.Serial('com10', 9600, timeout=1) as ser:
    while True:
        d = ser.read(1)
        out = bgphy.feed(d)

        #if len(out) > 0:
        #    print(out, flush=True)

        for p in out:
            decoded = bgdec.process_packet(p.payload)
            print(decoded, flush=True)

            if old_cellv != bgdec.data.cell_voltage_v:
                log.write(", ".join(["%f"%v for v in bgdec.data.cell_voltage_v]) + ", %d"%time.time() + " \n")
                log.flush()

            old_cellv = bgdec.data.cell_voltage_v[:]

log.close()