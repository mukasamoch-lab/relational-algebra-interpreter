import struct


PAGE_SIZE = 4096
HEADER_SIZE = 8
SLOT_SIZE = 4


class SlottedPage:
    def __init__(self, page_id, data=None):
        if data is None:
            data = bytearray(PAGE_SIZE)
            struct.pack_into("<IHH", data, 0, page_id, 0, PAGE_SIZE)
        self.data = data

    @property
    def free_bytes(self):
        _, slot_count, free_end = struct.unpack_from("<IHH", self.data, 0)
        return free_end - (HEADER_SIZE + SLOT_SIZE * slot_count)

    def insert(self, record):
        page_id, slot_count, free_end = struct.unpack_from("<IHH", self.data, 0)
        slot_offset = HEADER_SIZE + SLOT_SIZE * slot_count
        free_space = free_end - slot_offset

        if free_space < len(record) + SLOT_SIZE:
            return None

        record_offset = free_end - len(record)
        self.data[record_offset:free_end] = record
        struct.pack_into("<HH", self.data, slot_offset, record_offset, len(record))
        struct.pack_into("<IHH", self.data, 0, page_id, slot_count + 1, record_offset)
        return slot_count

    def get(self, slot_no):
        page_id, slot_count, _ = struct.unpack_from("<IHH", self.data, 0)

        if not 0 <= slot_no < slot_count:
            raise IndexError(f"record ({page_id}, {slot_no}): slot out of range")

        slot_offset = HEADER_SIZE + SLOT_SIZE * slot_no
        record_offset, length = struct.unpack_from("<HH", self.data, slot_offset)

        if record_offset == 0:
            raise ValueError(f"record ({page_id}, {slot_no}): slot is deleted")

        return bytes(self.data[record_offset:record_offset + length])

    def delete(self, slot_no):
        page_id, slot_count, _ = struct.unpack_from("<IHH", self.data, 0)

        if not 0 <= slot_no < slot_count:
            raise IndexError(f"record ({page_id}, {slot_no}): slot out of range")

        slot_offset = HEADER_SIZE + SLOT_SIZE * slot_no
        record_offset, _ = struct.unpack_from("<HH", self.data, slot_offset)

        if record_offset == 0:
            raise ValueError(f"record ({page_id}, {slot_no}): slot is already deleted")

        struct.pack_into("<HH", self.data, slot_offset, 0, 0)

    def live_records(self, copy_records=True):
        _, slot_count, _ = struct.unpack_from("<IHH", self.data, 0)
        for slot_no in range(slot_count):
            slot_offset = HEADER_SIZE + SLOT_SIZE * slot_no
            record_offset, length = struct.unpack_from("<HH", self.data, slot_offset)
            if record_offset != 0:
                view = memoryview(self.data)[record_offset:record_offset + length]
                yield slot_no, bytes(view) if copy_records else view