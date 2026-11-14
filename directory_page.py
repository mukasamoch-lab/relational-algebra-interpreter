import struct

from slotted_page import PAGE_SIZE


NO_NEXT_PAGE = 0xFFFFFFFF
HEADER = struct.Struct("<IIH")
ENTRY = struct.Struct("<IH")
MAX_ENTRIES = (PAGE_SIZE - HEADER.size) // ENTRY.size


class DirectoryPage:
    def __init__(self, data):
        self.data = data

    @classmethod
    def initialize(cls, frame):
        HEADER.pack_into(frame.data, 0, frame.page_id, NO_NEXT_PAGE, 0)
        return cls(frame.data)

    @property
    def entry_count(self):
        return HEADER.unpack_from(self.data)[2]

    def append_entry(self, data_page_id, free_bytes):
        index = self.entry_count
        if index == MAX_ENTRIES:
            raise ValueError("directory page is full")

        offset = HEADER.size + index * ENTRY.size
        ENTRY.pack_into(self.data, offset, data_page_id, free_bytes)
        struct.pack_into("<H", self.data, 8, index + 1)
        return index

    def entries(self):
        for index in range(self.entry_count):
            offset = HEADER.size + index * ENTRY.size
            yield ENTRY.unpack_from(self.data, offset)
            
    @property
    def next_page_id(self):
        return HEADER.unpack_from(self.data)[1]

    def set_next_page(self, page_id):
        struct.pack_into("<I", self.data, 4, page_id)

    def update_free_bytes(self, index, free_bytes):
        offset = HEADER.size + index * ENTRY.size
        struct.pack_into("<H", self.data, offset + 4, free_bytes)