from pathlib import Path

from slotted_page import PAGE_SIZE


class DiskManager:
    def __init__(self, path):
        self.path = Path(path)
        self.path.touch(exist_ok=True)
        self.reads = 0
        self.writes = 0

    def num_pages(self):
        size = self.path.stat().st_size
        if size % PAGE_SIZE != 0:
            raise ValueError("file size is not a multiple of the page size")
        return size // PAGE_SIZE

    def allocate_page(self):
        page_id = self.num_pages()
        with self.path.open("r+b") as file:
            file.truncate((page_id + 1) * PAGE_SIZE)
        return page_id

    def read_page(self, page_id):
        if not 0 <= page_id < self.num_pages():
            raise IndexError(f"page {page_id} is out of range")

        with self.path.open("rb") as file:
            file.seek(page_id * PAGE_SIZE)
            data = file.read(PAGE_SIZE)

        if len(data) != PAGE_SIZE:
            raise OSError(f"incomplete read of page {page_id}")

        self.reads += 1
        return data

    def write_page(self, page_id, data):
        if len(data) != PAGE_SIZE:
            raise ValueError(f"page must contain exactly {PAGE_SIZE} bytes")
        if not 0 <= page_id < self.num_pages():
            raise IndexError(f"page {page_id} is out of range")

        with self.path.open("r+b") as file:
            file.seek(page_id * PAGE_SIZE)
            file.write(data)

        self.writes += 1


    def reserve_pages(self, count):
        """Prepare a zero-filled trace file without measured read/write calls."""
        if count < 0 or self.num_pages() != 0:
            raise ValueError("reserve_pages requires an empty file and nonnegative count")
        with self.path.open("r+b") as file:
            file.truncate(count * PAGE_SIZE)


    def read_page_into(self, page_id, target):
        """Read into a reusable frame without allocating another page buffer."""
        if len(target) != PAGE_SIZE:
            raise ValueError("read target must be one page")
        if not 0 <= page_id < self.num_pages():
            raise IndexError(f"page {page_id} is out of range")
        with self.path.open("rb") as file:
            file.seek(page_id * PAGE_SIZE)
            count = file.readinto(target)
        if count != PAGE_SIZE:
            raise OSError(f"incomplete read of page {page_id}")
        self.reads += 1
