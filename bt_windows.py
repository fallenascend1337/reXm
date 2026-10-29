"""Windows transport: WinRT RFCOMM (plain Python Bluetooth sockets get 'host down' on Windows)."""
import uuid

from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothConnectionStatus, BluetoothDevice
from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId
from winrt.windows.devices.enumeration import DeviceInformation
from winrt.windows.networking.sockets import StreamSocket
from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions


class Transport:
    def __init__(self, dev, proto):
        self.dev = dev
        self.proto = proto
        self.name = dev.name
        a = f"{dev.bluetooth_address:012X}"
        self.mac = ":".join(a[i:i + 2] for i in range(0, 12, 2))
        self.sock = None

    async def connect(self, service_uuid):
        res = await self.dev.get_rfcomm_services_for_id_async(RfcommServiceId.from_uuid(service_uuid))
        if res.services.size == 0:
            raise OSError("Headphones not connected")
        svc = res.services[0]
        self.sock = StreamSocket()
        await self.sock.connect_async(svc.connection_host_name, svc.connection_service_name)
        self.writer = DataWriter(self.sock.output_stream)
        self.reader = DataReader(self.sock.input_stream)
        self.reader.input_stream_options = InputStreamOptions.PARTIAL

    async def write(self, data):
        self.writer.write_bytes(data)
        await self.writer.store_async()

    async def read(self):
        # never cancel this: cancelling a WinRT read closes the socket
        n = await self.reader.load_async(1024)
        return bytes(self.reader.read_byte() for _ in range(n))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


async def find(services):
    """services: [(proto, uuid)] in preference order. Returns (connected, Transport) for each paired Sony headset."""
    found = []
    selector = BluetoothDevice.get_device_selector_from_pairing_state(True)
    for info in await DeviceInformation.find_all_async_aqs_filter(selector):
        dev = await BluetoothDevice.from_id_async(info.id)
        if dev is None:
            continue
        for proto, sid in services:
            res = await dev.get_rfcomm_services_for_id_with_cache_mode_async(
                RfcommServiceId.from_uuid(sid), BluetoothCacheMode.CACHED)
            if res.services.size:
                found.append((dev.connection_status == BluetoothConnectionStatus.CONNECTED, Transport(dev, proto)))
                break
    return found
