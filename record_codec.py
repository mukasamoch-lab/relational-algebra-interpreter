import struct


def encode_record(schema, values):
    if len(schema) != len(values):
        raise ValueError("record has the wrong number of values")

    record = bytearray()
    for column, value in zip(schema, values):
        name, kind, *options = column

        if kind == "INT":
            if type(value) is not int or not -(2**31) <= value < 2**31:
                raise ValueError(f"{name} must be a signed 32-bit integer")
            record.extend(struct.pack("<i", value))

        elif kind == "VARCHAR":
            (max_length,) = options
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            encoded = value.encode("utf-8")
            if len(encoded) > max_length:
                raise ValueError(f"{name} exceeds VARCHAR({max_length})")
            record.extend(struct.pack("<H", len(encoded)))
            record.extend(encoded)

        else:
            raise ValueError(f"unsupported column type {kind!r}")

    return bytes(record)

def decode_record(schema, record):
    values = []
    offset = 0

    for column in schema:
        name, kind, *options = column

        if kind == "INT":
            if offset + 4 > len(record):
                raise ValueError(f"truncated INT field {name}")
            (value,) = struct.unpack_from("<i", record, offset)
            offset += 4

        elif kind == "VARCHAR":
            (max_length,) = options
            if offset + 2 > len(record):
                raise ValueError(f"missing length for VARCHAR field {name}")

            (length,) = struct.unpack_from("<H", record, offset)
            offset += 2
            if length > max_length:
                raise ValueError(f"{name} exceeds VARCHAR({max_length})")

            end = offset + length
            if end > len(record):
                raise ValueError(f"truncated VARCHAR field {name}")
            value = record[offset:end].decode("utf-8")
            offset = end

        else:
            raise ValueError(f"unsupported column type {kind!r}")

        values.append(value)

    if offset != len(record):
        raise ValueError("unexpected bytes after the last field")

    return tuple(values)