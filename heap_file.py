import struct

from directory_page import DirectoryPage, MAX_ENTRIES, NO_NEXT_PAGE
from slotted_page import HEADER_SIZE, PAGE_SIZE, SLOT_SIZE, SlottedPage


class HeapFile:
    def __init__(self, pool):
        self.pool = pool
        shared = getattr(pool, "shared", pool)
        if shared.capacity < 2:
            raise ValueError("heap files require at least two frames")
        if pool.disk.num_pages() == 0:
            frame = pool.new_page()
            try:
                DirectoryPage.initialize(frame)
            finally:
                pool.unpin_page(frame.page_id)

    def insert(self, record):
        required = len(record) + SLOT_SIZE
        if required > PAGE_SIZE - HEADER_SIZE:
            raise ValueError("record is too large for a data page")

        directory_id = 0
        while True:
            frame = self.pool.fetch_page(directory_id)
            directory = DirectoryPage(frame.data)
            dirty = False

            try:
                for index, (page_id, free_bytes) in enumerate(directory.entries()):
                    if free_bytes >= required:
                        data_frame = self.pool.fetch_page(page_id)
                        rid, remaining = self._insert_into_frame(data_frame, record)
                        directory.update_free_bytes(index, remaining)
                        dirty = True
                        return rid

                next_id = directory.next_page_id
                if next_id == NO_NEXT_PAGE:
                    if directory.entry_count < MAX_ENTRIES:
                        data_frame = self.pool.new_page()
                        struct.pack_into(
                            "<IHH", data_frame.data, 0, data_frame.page_id, 0, PAGE_SIZE
                        )

                        rid, remaining = self._insert_into_frame(data_frame, record)
                        directory.append_entry(rid[0], remaining)
                        dirty = True
                        return rid

                    next_frame = self.pool.new_page()
                    try:
                        DirectoryPage.initialize(next_frame)
                        next_id = next_frame.page_id
                        directory.set_next_page(next_id)
                        dirty = True
                    finally:
                        self.pool.unpin_page(next_frame.page_id)
            finally:
                self.pool.unpin_page(directory_id, is_dirty=dirty)

            directory_id = next_id

    def num_data_pages(self):
        count = 0
        directory_id = 0
        while directory_id != NO_NEXT_PAGE:
            frame = self.pool.fetch_page(directory_id)
            try:
                directory = DirectoryPage(frame.data)
                count += directory.entry_count
                directory_id = directory.next_page_id
            finally:
                self.pool.unpin_page(frame.page_id)
        return count

    def get(self, rid):
        page_id, slot_no = rid
        frame = self.pool.fetch_page(page_id)
        try:
            page = SlottedPage(page_id, frame.data)
            return page.get(slot_no)
        finally:
            self.pool.unpin_page(page_id)

    def delete(self, rid):
        page_id, slot_no = rid
        frame = self.pool.fetch_page(page_id)
        dirty = False
        try:
            page = SlottedPage(page_id, frame.data)
            page.delete(slot_no)
            dirty = True
        finally:
            self.pool.unpin_page(page_id, is_dirty=dirty)

    def scan(self):
        directory_id = 0
        while directory_id != NO_NEXT_PAGE:
            frame = self.pool.fetch_page(directory_id)
            try:
                directory = DirectoryPage(frame.data)
                next_id = directory.next_page_id
                for page_id, _ in directory.entries():
                    data_frame = self.pool.fetch_page(page_id)
                    try:
                        page = SlottedPage(page_id, data_frame.data)
                        for slot_no, record in page.live_records():
                            yield (page_id, slot_no), record
                    finally:
                        self.pool.unpin_page(page_id)
            finally:
                self.pool.unpin_page(directory_id)

            directory_id = next_id

    def _insert_into_frame(self, frame, record):
        dirty = False
        try:
            page = SlottedPage(frame.page_id, frame.data)
            slot_no = page.insert(record)
            if slot_no is None:
                raise RuntimeError("directory free space does not match the data page")
            dirty = True
            return (frame.page_id, slot_no), page.free_bytes
        finally:
            self.pool.unpin_page(frame.page_id, is_dirty=dirty)