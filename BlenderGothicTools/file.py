# file.py: File operations.
# This file also provides GUI to setup the list of the texture directories.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# Author: Vitaly Baranov
# License: GPL
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import os
from io import BytesIO
from pathlib import Path
from struct import pack, unpack
from typing import BinaryIO
from mathutils import Matrix, Vector


def file_exists(file_path):
    """Does the file exist? Returns True or False."""
    return os.path.exists(file_path)


def delete_file(filename):
    """Deletes the file if it exists."""
    try:
        return os.remove(filename)
    except Exception as err:
        print("Error", __name__, f"delete_file(filename={filename})", err, sep=" --> ")
        return None


def get_file_size(filename):
    """Returns the size of the file, in bytes."""
    try:
        return os.path.getsize(filename)
    except Exception as err:
        print("Error", __name__, f"get_file_size(filename={filename})", err, sep=" --> ")

        return 0


def get_encoding():
    """Get current encoding."""
    return "Windows-1250"


# Readonly byte string manipulation class
class bytebuffer:
    __slots__ = ("stream", "size", "pos")

    def __init__(self, buffer: bytes):
        self.stream: BytesIO = BytesIO(buffer)
        self.size = len(buffer)
        self.pos = 0

    def move_buffer_pos(self, offset):
        pos = self.pos + offset
        if self.size < pos:
            self.size = pos
        self.pos = pos

    def set_pos(self, pos):
        if pos == self.pos:
            return

        if not 0 <= pos <= self.size:
            raise RuntimeError(
                f"Attempt to seek file pointer out of byte string buffer.\n"
                f'Position of pointer: {"0x" + hex(pos)[2:].upper()}.\n'
                f'Allowable range: {"0x" + hex(0)[2:].upper()}..{"0x" + hex(self.size)[2:].upper()}.'
            )

        self.stream.seek(pos)
        self.pos = pos

    def skip_by_offset(self, offset):
        self.set_pos(self.pos + offset)

    def remaining(self):
        return self.size - self.pos

    def eob(self):
        return self.pos == self.size

    def read_data(self, pattern, size):
        """
        Reads data defined by unpack pattern and size.
        Check: https://docs.python.org/3/library/struct.html#format-characters
        """
        buf = self.stream.read(size)

        if len(buf) != size:
            raise RuntimeError(
                f"Could not read data from byte string sequence.\n"
                f'Position in buffer: {"0x" + hex(self.pos)[2:].upper()}.\n'
                f"Size of data: {size}."
            )

        self.move_buffer_pos(size)
        return unpack(f"<{pattern}", buf)

    def read_signed_char(self):
        """Reads signed 1-byte (b) integer."""
        return self.read_data("b", 1)[0]

    def read_unsigned_char(self):
        """Reads unsigned 1-byte (B) integer."""
        return self.read_data("B", 1)[0]

    def read_bool(self):
        return self.read_unsigned_char() != 0

    def read_signed_short(self):
        """Reads signed 2-byte (h) integer."""
        return self.read_data("h", 2)[0]

    def read_unsigned_short(self):
        """Reads unsigned 2-byte (H) integer."""
        return self.read_data("H", 2)[0]

    def read_signed_long(self):
        """Reads signed 4-byte (i) integer."""
        return self.read_data("i", 4)[0]

    def read_unsigned_long(self):
        """Reads unsigned 4-byte (I) integer."""
        return self.read_data("I", 4)[0]

    def read_float(self):
        """Reads 4-byte (f) floating point number."""
        return self.read_data("f", 4)[0]

    def read_double(self):
        """Reads 8-byte (d) floating point number."""
        return self.read_data("d", 4)[0]

    def get_line(self):
        """Reads string until theres special sep"""
        tmp = ""

        char = self.read_string(buffer_size=1, terminator=None)
        while char != "\n" and char != "\r" and char != "\0":
            tmp += char
            char = self.read_string(buffer_size=1, terminator=None)

        return tmp

    def read_vec3(self):
        return Vector((self.read_float(), self.read_float(), self.read_float()))

    def read_mat3x3(self):
        buf = list()
        for i in range(9):
            buf.append(self.read_float())

        return Matrix(
            (
                [buf[0], buf[1], buf[2]],
                [buf[3], buf[4], buf[5]],
                [buf[6], buf[7], buf[8]],
            )
        ).transposed()

    def read_mat4x4(self):
        buf = list()
        for i in range(16):
            buf.append(self.read_float())

        return Matrix(
            (
                [buf[0], buf[1], buf[2], buf[3]],
                [buf[4], buf[5], buf[6], buf[7]],
                [buf[8], buf[9], buf[10], buf[11]],
                [buf[12], buf[13], buf[14], buf[15]],
            )
        ).transposed()

    def read_string(self, buffer_size=512, terminator=b"\x00"):
        """Reads ANSI string with custom terminator (defaults to zero character NUL) and buffer size."""
        pos = self.stream.tell()
        buf = self.stream.read(buffer_size)
        string_len = buffer_size
        size = buffer_size
        if terminator:
            string_len = buf.find(terminator)
            if string_len == -1:
                raise RuntimeError(
                    f"Could not skip a terminated string from buffer.\n"
                    f"The string seems to be too long.\n"
                    f'Terminator: "{terminator}".\n'
                    f'Position in file: {"0x" + hex(self.pos)[2:].upper()}.'
                )
            size = string_len + 1

        self.set_pos(pos + size)
        return buf[:string_len].decode(get_encoding(), errors="replace")

    def read_bytes(self, buffer_size=512):
        """Reads byte sequence with buffer_size."""
        buf = self.stream.read(buffer_size)
        self.move_buffer_pos(buffer_size)
        return buf

    def skip_string(self, buffer_size=512, terminator=b"\x00"):
        """Reads ANSI string with custom terminator (defaults to zero character NUL) and buffer size."""
        pos = self.stream.tell()
        buf = self.stream.read(buffer_size)

        string_len = buffer_size
        size = buffer_size
        if terminator:
            string_len = buf.find(terminator)
            if string_len == -1:
                raise RuntimeError(
                    f"Could not skip a terminated string from buffer.\n"
                    f"The string seems to be too long.\n"
                    f'Terminator: "{terminator}".\n'
                    f'Position in file: {"0x" + hex(self.pos)[2:].upper()}.'
                )
            size = string_len + 1
        self.set_pos(pos + size)


class TFile:
    __slots__ = ("__stream", "__name", "__mode", "__path", "__size", "__pos")

    def __init__(self):
        self.__stream: BinaryIO = None
        self.__name = ""
        self.__mode = ""
        self.__path: Path = None
        self.__size = 0
        self.__pos = 0

    def __MoveFilePos(self, offset):
        self.__pos = self.__pos + offset
        if self.__size < self.__pos:
            self.__size = self.__pos

    def IsOpened(self):
        if self.__stream:
            if self.__stream.closed:
                raise RuntimeError(f"{__name__}: VALID STREAM FOR CLOSED FILE!")
            return True
        return False

    def Open(self, filename, mode):  # I really fucking hate this class AAAAAAAAAAAAAAAAAAAAA
        if self.IsOpened():
            self.Close()

        if "r" in mode:
            file_size = get_file_size(filename)
            if file_size == 0:
                raise RuntimeError(f'{__name__}: File is empty.\nFile path: "{filename}".')
        else:
            file_size = 0

        try:
            self.__stream = open(filename, mode.replace("t", "b"))
        except Exception as err:
            print("Error", __name__, f"TFile.Open(filename={filename}, open_mode={mode})", err, sep=" --> ")
            mode = "write" if "w" in mode else "read"
            raise RuntimeError(f'{__name__}: Could not open file for {mode}.\nFile path: "{filename}".')

        self.__path = Path(filename)
        self.__name = self.__path.stem
        self.__mode = mode
        self.__size = file_size
        self.__pos = 0

    def Close(self):
        if self.IsOpened():
            self.__stream.close()
            self.__init__()

    def GetName(self):
        return self.__name

    def GetMode(self):
        return self.__mode

    def GetPath(self):
        return self.__path

    def GetSize(self):
        return self.__size

    def GetPos(self):
        return self.__pos

    def SkipByOffset(self, offset):
        self.SetPos(self.__pos + offset)

    def SetPos(self, pos):
        if pos == self.__pos:
            return

        if not 0 <= pos <= self.__size:
            raise RuntimeError(
                f"{__name__}:"
                f"Attempt to seek file pointer out of file.\n"
                f'File path: "{self.__name}".\n'
                f'Position of file pointer: {"0x" + hex(pos)[2:].upper()}.\n'
                f'Allowable range: {"0x" + hex(0)[2:].upper()}..{"0x" + hex(self.__size)[2:].upper()}.'
            )

        self.__stream.seek(pos)
        self.__pos = pos

    def Eof(self):
        return self.__pos == self.__size

    def __raise_write_error(self, data, pattern):
        raise RuntimeError(
            f"{__name__}:"
            f"""
            Could not write data structured as ({pattern}) to file.\n
            Check stacktrace for more information.
            File path: '{self.__name}'.
            Data: {pack(f'<{pattern}', data)}.
            """
        )

    def WriteData(self, data, pattern, size):
        """
        Writes data defined by unpack pattern and size.
        Check: https://docs.python.org/3/library/struct.html#format-characters
        """
        try:
            self.__stream.write(pack(f"<{pattern}", data))
        except EnvironmentError:
            raise RuntimeError(
                f"{__name__}:"
                f"Could not write data to file.\n"
                f'File path: "{self.__name}".\n'
                f"Size of data: {size}."
            )

        self.__MoveFilePos(size)

    def WriteSignedChar(self, data):
        """Writes signed 1-byte (b) integer."""
        pattern = "b"
        if not -128 <= data <= 127:
            self.__raise_write_error(data, pattern)
        self.WriteData(data, pattern, 1)

    def WriteUnsignedChar(self, data):
        """Writes unsigned 1-byte (B) integer."""
        pattern = "B"
        if not 0 <= data <= 255:
            self.__raise_write_error(data, pattern)
        self.WriteData(data, pattern, 1)

    def WriteBool(self, data):
        self.WriteUnsignedChar(int(bool(data)))

    def WriteSignedShort(self, data):
        """Writes signed 2-byte (h) integer."""
        pattern = "h"
        if data < -32768 or data > 32767:
            self.__raise_write_error(data, pattern)
        self.WriteData(data, pattern, 2)

    def WriteUnsignedShort(self, data):
        """Writes unsigned 2-byte (H) integer."""
        pattern = "H"
        if not 0 <= data <= 65535:
            self.__raise_write_error(data, pattern)
        self.WriteData(data, pattern, 2)

    def WriteSignedLong(self, data):
        """Writes signed 4-byte (i) integer."""
        self.WriteData(data, "i", 4)

    def WriteUnsignedLong(self, data):
        """Writes unsigned 4-byte (I) integer."""
        self.WriteData(data, "I", 4)

    def WriteFloat(self, data):
        """Writes 4-byte (f) floating point number."""
        self.WriteData(data, "f", 4)

    def WriteDouble(self, data):
        """Writes 8-byte (d) floating point number."""
        self.WriteData(data, "d", 8)

    def WriteString(self, string):
        """Writes ANSI string with terminating zero character."""
        size = len(string) + 1
        try:
            buf = string.encode(get_encoding(), errors="replace") + b"\x00"
            self.__stream.write(buf)
        except EnvironmentError:
            raise RuntimeError(
                f'{__name__}: Could not write data to file.\nFile path: "{self.__name}".\nSize of data: {size}.'
            )
        self.__MoveFilePos(size)

    def WriteLine(self, line):
        """Writes ANSI string with terminating CR character."""
        size = len(line) + 2
        try:
            buf = (line + os.linesep).encode(get_encoding(), errors="replace")
            self.__stream.write(buf)
        except EnvironmentError:
            raise RuntimeError(
                f'{__name__}: Could not write data to file.\nFile path: "{self.__name}".\nSize of data: {size}.'
            )

        self.__MoveFilePos(size)

    def ReadData(self, pattern, size):
        """
        Reads data defined by unpack pattern and size.
        Check: https://docs.python.org/3/library/struct.html#format-characters
        """
        buf = self.__stream.read(size)

        if len(buf) != size:
            raise RuntimeError(
                f"{__name__}:"
                f"Could not read data from file.\n"
                f'File path: "{self.__name}".\n'
                f'Position in file: {"0x" + hex(self.__pos)[2:].upper()}.\n'
                f"Size of data: {size}."
            )

        self.__MoveFilePos(size)
        return unpack(f"<{pattern}", buf)

    def ReadSignedChar(self):
        """Reads signed 1-byte (b) integer."""
        return self.ReadData("b", 1)[0]

    def ReadUnsignedChar(self):
        """Reads unsigned 1-byte (B) integer."""
        return self.ReadData("B", 1)[0]

    def ReadBool(self):
        return self.ReadUnsignedChar() != 0

    def ReadSignedShort(self):
        """Reads signed 2-byte (h) integer."""
        return self.ReadData("h", 2)[0]

    def ReadUnsignedShort(self):
        """Reads unsigned 2-byte (H) integer."""
        return self.ReadData("H", 2)[0]

    def ReadSignedLong(self):
        """Reads signed 4-byte (i) integer."""
        return self.ReadData("i", 4)[0]

    def ReadUnsignedLong(self):
        """Reads unsigned 4-byte (I) integer."""
        return self.ReadData("I", 4)[0]

    def ReadFloat(self):
        """Reads 4-byte (f) floating point number."""
        return self.ReadData("f", 4)[0]

    def ReadDouble(self):
        """Reads 8-byte (d) floating point number."""
        return self.ReadData("d", 4)[0]

    def ReadString(self, buffer_size=512, terminator=b"\x00"):
        """Reads ANSI string with custom terminator (defaults to zero character NUL) and buffer size."""
        pos = self.__stream.tell()
        buf = self.__stream.read(buffer_size)
        string_len = buffer_size
        size = buffer_size
        if terminator:
            string_len = buf.find(terminator)
            if string_len == -1:
                raise RuntimeError(
                    f"{__name__}:"
                    f"Could not read a terminated string from file.\n"
                    f"The string seems to be too long.\n"
                    f'File path: "{self.__name}".\n'
                    f'Terminator: "{terminator}".\n'
                    f'Position in file: {"0x" + hex(self.__pos)[2:].upper()}.'
                )
            size = string_len + 1

        self.SetPos(pos + size)
        return buf[:string_len].decode(get_encoding(), errors="replace")

    def ReadBytes(self, buffer_size=512):
        """Reads byte sequence with buffer_size."""
        buf = self.__stream.read(buffer_size)
        self.__MoveFilePos(buffer_size)
        return buf

    def SkipString(self, buffer_size=512, terminator=b"\x00"):
        """Reads ANSI string with custom terminator (defaults to zero character NUL) and buffer size."""
        pos = self.__stream.tell()
        buf = self.__stream.read(buffer_size)

        string_len = buffer_size
        size = buffer_size
        if terminator:
            string_len = buf.find(terminator)
            if string_len == -1:
                raise RuntimeError(
                    f"{__name__}:"
                    f"Could not skip a terminated string from file.\n"
                    f"The string seems to be too long.\n"
                    f'File path: "{self.__name}".\n'
                    f'Terminator: "{terminator}".\n'
                    f'Position in file: {"0x" + hex(self.__pos)[2:].upper()}.'
                )
            size = string_len + 1

        self.SetPos(pos + size)

    def ReadLine(self):
        """Reads ANSI string terminated CR character."""

        pos = self.__stream.tell()
        buf = self.__stream.read(512)
        string_len = buf.find(b"\n")

        if string_len == -1:
            string_len = buf.find(b"\r")

        if string_len != -1:
            self.__stream.seek(pos + string_len + 1)
            buf = buf[:string_len]

        line = buf.decode(get_encoding(), errors="replace").rstrip("\r\n")
        if line is None:
            raise RuntimeError(
                f"{__name__}:"
                f"Could not read a CR+LF ended line from file.\n"
                f"The line seems to be too long.\n"
                f'File path: "{self.__name}".\n'
                f'Position in file: {"0x" + hex(self.__pos)[2:].upper()}.'
            )

        self.__pos = self.__stream.tell()
        return line

    def SkipLine(self):
        """Skips ANSI string terminated CR character."""

        pos = self.__stream.tell()
        buf = self.__stream.read(512)
        string_len = buf.find(b"\n")

        if string_len == -1:
            string_len = buf.find(b"\r")

        if string_len != -1:
            self.__stream.seek(pos + string_len + 1)
        self.__pos = self.__stream.tell()
