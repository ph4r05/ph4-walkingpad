import functools
import itertools
import os
import codecs
from operator import methodcaller, sub


"""
https://stackoverflow.com/questions/2301789/how-to-read-a-file-in-reverse-order
"""


def ceil_div(num, denom):
    return -(-num // denom)


def split(string, separator, keep_separator):
    """
    Splits given string by given separator.
    """
    parts = string.split(separator)
    if keep_separator:
        *parts, last_part = parts
        parts = [part + separator for part in parts]
        if last_part:
            return parts + [last_part]
    return parts


def read_batch_from_end(byte_stream, size, end_position):
    """
    Reads batch from the end of given byte stream.
    """
    if end_position > size:
        offset = end_position - size
    else:
        offset = 0
        size = end_position
    byte_stream.seek(offset)
    return byte_stream.read(size)


def reverse_binary_stream(byte_stream, batch_size=None,
                          lines_separator=None,
                          keep_lines_separator=True):
    if lines_separator is None:
        lines_separator = (b'\r', b'\n', b'\r\n')
        lines_splitter = methodcaller(str.splitlines.__name__,
                                      keep_lines_separator)
    else:
        lines_splitter = functools.partial(split,
                                           separator=lines_separator,
                                           keep_separator=keep_lines_separator)
    stream_size = byte_stream.seek(0, os.SEEK_END)
    if batch_size is None:
        batch_size = stream_size or 1
    batches_count = ceil_div(stream_size, batch_size)
    remaining_bytes_indicator = itertools.islice(
            itertools.accumulate(itertools.chain([stream_size],
                                                 itertools.repeat(batch_size)),
                                 sub),
            batches_count)
    try:
        remaining_bytes_count = next(remaining_bytes_indicator)
    except StopIteration:
        return

    def read_batch(position):
        result = read_batch_from_end(byte_stream,
                                     size=batch_size,
                                     end_position=position)
        while result.startswith(lines_separator):
            try:
                position = next(remaining_bytes_indicator)
            except StopIteration:
                break
            result = (read_batch_from_end(byte_stream,
                                          size=batch_size,
                                          end_position=position)
                      + result)
        return result

    batch = read_batch(remaining_bytes_count)
    segment, *lines = lines_splitter(batch)
    yield from reversed(lines)
    for remaining_bytes_count in remaining_bytes_indicator:
        batch = read_batch(remaining_bytes_count)
        lines = lines_splitter(batch)
        if batch.endswith(lines_separator):
            yield segment
        else:
            lines[-1] += segment
        segment, *lines = lines
        yield from reversed(lines)
    yield segment


def reverse_file(file, batch_size=None,
                 lines_separator=None,
                 keep_lines_separator=True):
    encoding = file.encoding
    if lines_separator is not None:
        lines_separator = lines_separator.encode(encoding)
    yield from map(functools.partial(codecs.decode,
                                     encoding=encoding),
                   reverse_binary_stream(
                           file.buffer,
                           batch_size=batch_size,
                           lines_separator=lines_separator,
                           keep_lines_separator=keep_lines_separator))

