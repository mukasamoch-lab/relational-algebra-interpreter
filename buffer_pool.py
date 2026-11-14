from slotted_page import PAGE_SIZE
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Frame:
    page_id: int
    data: bytearray
    pin_count: int = 0
    dirty: bool = False
    referenced: bool = True
    disk: object = None


class BufferPool:
    def __init__(self, disk, capacity, policy="lru"):
        if capacity < 1:
            raise ValueError("buffer pool capacity must be at least 1")
        if policy not in ("lru", "clock"):
            raise ValueError("policy must be lru or clock")

        self.disk = disk
        self.disks = {str(disk.path.resolve()): disk}
        self.capacity = capacity
        self.policy = policy
        self.frames = OrderedDict()
        self.clock_slots = []
        self.clock_hand = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.retired_reads = 0
        self.retired_writes = 0
        self.peak_frames = 0
        self.reservations = []
        self.peak_accounted_frames = 0

    def for_file(self, disk):
        name = str(disk.path.resolve())
        registered = self.disks.setdefault(name, disk)
        return FileBufferPool(self, registered)

    def _key(self, page_id, disk):
        if disk is self.disk:
            return page_id
        return (str(disk.path.resolve()), page_id)

    def fetch_page(self, page_id, disk=None):
        disk = self.disk if disk is None else disk
        key = self._key(page_id, disk)
        frame = self.frames.get(key)
        if frame is not None:
            frame.pin_count += 1
            frame.referenced = True
            self.frames.move_to_end(key)
            self.hits += 1
            return frame

        if not 0 <= page_id < disk.num_pages():
            raise IndexError(f"page {page_id} is out of range")
        victim, clock_slot = self._prepare_slot()
        data = victim.data if victim is not None else bytearray(PAGE_SIZE)
        try:
            disk.read_page_into(page_id, data)
        except OSError:
            # A short read may have overwritten a victim. Never keep it cached.
            if victim is not None:
                del self.frames[self._key(victim.page_id, victim.disk)]
                if self.policy == "clock":
                    self.clock_slots.pop(clock_slot)
                    self.clock_hand = 0
            raise
        frame = Frame(page_id, data, pin_count=1, disk=disk)
        self._install_frame(frame, victim, clock_slot)

        self.misses += 1
        if victim is not None:
            self.evictions += 1
        return frame

    def new_page(self, disk=None):
        disk = self.disk if disk is None else disk
        victim, clock_slot = self._prepare_slot()
        page_id = disk.allocate_page()
        data = bytearray(PAGE_SIZE) if victim is None else victim.data
        if victim is not None:
            for index in range(PAGE_SIZE):
                data[index] = 0
        frame = Frame(page_id, data, pin_count=1, dirty=True, disk=disk)
        self._install_frame(frame, victim, clock_slot)
        return frame

    def _prepare_slot(self):
        limit = self.capacity - max(self.reservations, default=0)
        if limit < 1:
            raise RuntimeError("no frame available within the working-memory budget")
        if len(self.frames) < limit:
            return None, None

        clock_slot = None
        if self.policy == "clock":
            clock_slot = self._clock_victim()
            victim = (
                self.frames[self.clock_slots[clock_slot]]
                if clock_slot is not None else None
            )
        else:
            victim = next(
                (frame for frame in self.frames.values() if frame.pin_count == 0),
                None,
            )

        if victim is None:
            raise RuntimeError("no unpinned frame available")
        if victim.dirty:
            victim.disk.write_page(victim.page_id, victim.data)
            victim.dirty = False
        return victim, clock_slot

    def _install_frame(self, frame, victim, clock_slot):
        if victim is not None:
            del self.frames[self._key(victim.page_id, victim.disk)]
        key = self._key(frame.page_id, frame.disk)
        self.frames[key] = frame
        self.peak_frames = max(self.peak_frames, len(self.frames))
        self.peak_accounted_frames = max(
            self.peak_accounted_frames,
            len(self.frames) + max(self.reservations, default=0),
        )

        if self.policy == "clock":
            if clock_slot is None:
                self.clock_slots.append(key)
            else:
                self.clock_slots[clock_slot] = key
                self.clock_hand = (clock_slot + 1) % len(self.clock_slots)

    def _clock_victim(self):
        slot = self.clock_hand
        for _ in range(2 * self.capacity):
            frame = self.frames[self.clock_slots[slot]]
            if frame.pin_count == 0:
                if not frame.referenced:
                    return slot
                frame.referenced = False

            slot = (slot + 1) % len(self.clock_slots)

        return None

    def unpin_page(self, page_id, is_dirty=False, disk=None):
        disk = self.disk if disk is None else disk
        frame = self.frames.get(self._key(page_id, disk))
        if frame is None or frame.pin_count == 0:
            raise ValueError(f"page {page_id} is not pinned")

        frame.pin_count -= 1
        frame.dirty = frame.dirty or is_dirty

    def stats(self):
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "reads": self.retired_reads + sum(disk.reads for disk in self.disks.values()),
            "writes": self.retired_writes + sum(disk.writes for disk in self.disks.values()),
        }

    @contextmanager
    def working_memory(self, pages=1):
        """Reserve a streaming tuple workspace shared along a unary pipeline."""
        previous = max(self.reservations, default=0)
        required = max(previous, pages)
        limit = self.capacity - required
        if limit < 1:
            raise RuntimeError("working-memory request exceeds the frame budget")
        while len(self.frames) > limit:
            victim, clock_slot = self._prepare_slot_for_trim()
            key = self._key(victim.page_id, victim.disk)
            del self.frames[key]
            if self.policy == "clock":
                self.clock_slots.pop(clock_slot)
                self.clock_hand = 0
        self.reservations.append(pages)
        self.peak_accounted_frames = max(
            self.peak_accounted_frames, len(self.frames) + required
        )
        try:
            yield
        finally:
            self.reservations.pop()

    def _prepare_slot_for_trim(self):
        if self.policy == "clock":
            slot = self._clock_victim()
            victim = self.frames[self.clock_slots[slot]] if slot is not None else None
        else:
            slot = None
            victim = next(
                (frame for frame in self.frames.values() if frame.pin_count == 0), None
            )
        if victim is None:
            raise RuntimeError("no unpinned frame available for working memory")
        if victim.dirty:
            victim.disk.write_page(victim.page_id, victim.data)
            victim.dirty = False
        return victim, slot

    def discard_file(self, disk):
        """Release temporary frames while retaining their cumulative I/O totals."""
        if disk is self.disk:
            raise ValueError("cannot discard the default database file")
        keys = [key for key, frame in self.frames.items() if frame.disk is disk]
        if any(self.frames[key].pin_count for key in keys):
            raise RuntimeError("cannot discard a file with pinned frames")
        for key in keys:
            frame = self.frames[key]
            if frame.dirty:
                disk.write_page(frame.page_id, frame.data)
            del self.frames[key]
        if self.policy == "clock":
            self.clock_slots = [key for key in self.clock_slots if key not in keys]
            self.clock_hand = 0
        name = str(disk.path.resolve())
        if self.disks.pop(name, None) is not None:
            self.retired_reads += disk.reads
            self.retired_writes += disk.writes

    def flush_all(self):
        for frame in self.frames.values():
            if frame.dirty:
                frame.disk.write_page(frame.page_id, frame.data)
                frame.dirty = False


class FileBufferPool:
    """A file-bound view of the same shared frame array and replacement policy."""

    def __init__(self, shared, disk):
        self.shared = shared
        self.disk = disk

    def fetch_page(self, page_id):
        return self.shared.fetch_page(page_id, disk=self.disk)

    def new_page(self):
        return self.shared.new_page(disk=self.disk)

    def unpin_page(self, page_id, is_dirty=False):
        self.shared.unpin_page(page_id, is_dirty, disk=self.disk)

    def flush_all(self):
        self.shared.flush_all()

    def stats(self):
        return self.shared.stats()
