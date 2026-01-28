#
# Demo of using a FTDI cable to communicate with the battery - this cable is already available for
# programming Spektrum devices. Hopefully won't blow up your computer but it might, see README file
# for critical warnings and disclaimers.
#

# If using a regular serial cable - replace the following with a PySerial call to open the serial port.
# The "port" object will just use write & read commands, so PySerial should work just as well.

use_ftdi_cable = True

if use_ftdi_cable:
    # IMPORTANT: You MUST install https://github.com/mariusgreuel/pyftdiwin
    #            NOT plain pyftdi for this to work on Windows. You'll need to remove pyftdi first
    #            if already installed. Otherwise you need to force the ftid cable to use a different
    #            drive which will BREAK the normal radio programming software.

    from pyftdi.ftdi import Ftdi
    Ftdi.add_custom_product(Ftdi.DEFAULT_VENDOR, 0x7c48)
    import pyftdi.serialext
    port = pyftdi.serialext.serial_for_url('ftdi://ftdi:0x7c48/1', baudrate=9600, timeout=2.0)
else:
    import serial
    port = serial.Serial('com10', 9600, timeout=1)

import time
from pybattgo import BattGoDecoder, BattGoPhyDecoder, BattGoPhyTx

bgphy = BattGoPhyDecoder()
bgdec = BattGoDecoder()
bgtx = BattGoPhyTx(port)

state = "discovery"

last_good_time = time.time()

# The following has a hard-coded battery address of E2, picked as saw it used in a protocol capture

while True:

    if state == "discovery":
        bgtx.send_packet(0x01, 0x00, b"\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")

    elif state == "wait_address":
        #This request is done elsewhere
        pass

    elif state == "manuf_info":
        # Get manufacturer data
        bgtx.send_packet(0x01, 0xE2, b"\x88")

    elif state == "user_info":
        bgtx.send_packet(0x01, 0xE2, b"\x42")

    elif state == "status":
        cells = bgdec.data.battery_number_of_cells

        # Status request for all cells - we need to specify how many cells we want data for in the request
        bgtx.send_packet(0x01, 0xE2, b"\x44\x00" + bytes([(cells-1)]) + b"\x00")

    data = port.read(256)
    for p in bgphy.feed(data):

        # NOTE: You see your own "echo" on every sent packet, due to the hardware TX/RX feedback
        # Comment out this call for less chatty thing
        print("%02x %02x %d :"%(p.addr_source, p.addr_dest, p.checksum_ok) + p.payload.hex(), flush=True)

        if p.checksum_ok:

            # These messages are only during setup/discovery phase
            if p.addr_source == 0x00 and p.addr_dest == 0x01:
                if p.payload[0] == 0x03:
                    sn = p.payload[1:]

                    # I would like you to use address 0xE2, send a ping request
                    # with that address specified
                    bgtx.send_packet(0x01, 0x00, b"\x02\xE2" + sn)
                    state = "wait_address"

                    # Avoid setting back to discovery too quickly
                    last_good_time = time.time()

            # Once battery uses it's assigned address we are talking regularly
            if p.addr_source == 0xe2 and p.addr_dest == 0x01:

                # pong response WITH device address accepted,
                # now we can talk to it normally. Request battery info first.
                if p.payload[0] == 0x03:
                    state = "manuf_info"

                # 0x89 is manufacturer info response, next request user info
                elif p.payload[0] == 0x89:
                    state = "user_info"

                # 0x43 is user info response, next request status
                elif p.payload[0] == 0x43:
                    state = "status"

                last_good_time = time.time()

            decoded = bgdec.process_packet(p.payload)

            # Print every decoded message
            print(decoded)

        # If we haven't seen a good response in a while go back to discovery, user may have unplugged battery
        if time.time() - last_good_time > 5:
            state = "discovery"

    time.sleep(0.5)
