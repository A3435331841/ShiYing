"""ISAAC64 stream cipher for WeChat Channels video decrypt.

Translates the Go implementation:
https://github.com/oliver-zch/wx-video-channel-download/blob/main/pkg/decrypt/decrypt.go
https://github.com/Hanson/WechatSphDecrypt/blob/main/decrypt.go
"""
import struct

class ISAAC64:
    MASK64 = 0xFFFFFFFFFFFFFFFF

    def __init__(self, key: int):
        self.rand_cnt = 255
        self.aa = 0
        self.bb = 0
        self.cc = 0
        self.seed = [0] * 256
        self.mm = [0] * 256
        self._init(key)

    def _init(self, key: int):
        golden = 0x9E3779B97F4A7C13
        a = b = c = d = e = f = g = h = golden
        self.seed[0] = key & self.MASK64

        for _ in range(4):
            a, b, c, d, e, f, g, h = self._mix(a, b, c, d, e, f, g, h)

        for i in range(0, 256, 8):
            a = (a + self.seed[i]) & self.MASK64
            b = (b + self.seed[i+1]) & self.MASK64
            c = (c + self.seed[i+2]) & self.MASK64
            d = (d + self.seed[i+3]) & self.MASK64
            e = (e + self.seed[i+4]) & self.MASK64
            f = (f + self.seed[i+5]) & self.MASK64
            g = (g + self.seed[i+6]) & self.MASK64
            h = (h + self.seed[i+7]) & self.MASK64
            a, b, c, d, e, f, g, h = self._mix(a, b, c, d, e, f, g, h)
            self.mm[i] = a
            self.mm[i+1] = b
            self.mm[i+2] = c
            self.mm[i+3] = d
            self.mm[i+4] = e
            self.mm[i+5] = f
            self.mm[i+6] = g
            self.mm[i+7] = h

        for i in range(0, 256, 8):
            a = (a + self.mm[i]) & self.MASK64
            b = (b + self.mm[i+1]) & self.MASK64
            c = (c + self.mm[i+2]) & self.MASK64
            d = (d + self.mm[i+3]) & self.MASK64
            e = (e + self.mm[i+4]) & self.MASK64
            f = (f + self.mm[i+5]) & self.MASK64
            g = (g + self.mm[i+6]) & self.MASK64
            h = (h + self.mm[i+7]) & self.MASK64
            a, b, c, d, e, f, g, h = self._mix(a, b, c, d, e, f, g, h)
            self.mm[i] = a
            self.mm[i+1] = b
            self.mm[i+2] = c
            self.mm[i+3] = d
            self.mm[i+4] = e
            self.mm[i+5] = f
            self.mm[i+6] = g
            self.mm[i+7] = h

        self._isaac64()

    def _isaac64(self):
        self.cc = (self.cc + 1) & self.MASK64
        self.bb = (self.bb + self.cc) & self.MASK64

        for i in range(256):
            rem = i % 4
            if rem == 0:
                self.aa = (~(self.aa ^ ((self.aa << 21) & self.MASK64))) & self.MASK64
            elif rem == 1:
                self.aa = (self.aa ^ (self.aa >> 5)) & self.MASK64
            elif rem == 2:
                self.aa = (self.aa ^ ((self.aa << 12) & self.MASK64)) & self.MASK64
            elif rem == 3:
                self.aa = (self.aa ^ (self.aa >> 33)) & self.MASK64

            self.aa = (self.aa + self.mm[(i + 128) % 256]) & self.MASK64
            x = self.mm[i]
            y = (self.mm[(x >> 3) % 256] + self.aa + self.bb) & self.MASK64
            self.mm[i] = y
            self.bb = (self.mm[(y >> 11) % 256] + x) & self.MASK64
            self.seed[i] = self.bb

    def _mix(self, a, b, c, d, e, f, g, h):
        m = self.MASK64
        a = (a - e) & m
        f = (f ^ (h >> 9)) & m
        h = (h + a) & m
        b = (b - f) & m
        g = (g ^ ((a << 9) & m)) & m
        a = (a + b) & m
        c = (c - g) & m
        h = (h ^ (b >> 23)) & m
        b = (b + c) & m
        d = (d - h) & m
        a = (a ^ ((c << 15) & m)) & m
        c = (c + d) & m
        e = (e - a) & m
        b = (b ^ (d >> 14)) & m
        d = (d + e) & m
        f = (f - b) & m
        c = (c ^ ((e << 20) & m)) & m
        e = (e + f) & m
        g = (g - c) & m
        d = (d ^ (f >> 17)) & m
        f = (f + g) & m
        h = (h - d) & m
        e = (e ^ ((g << 14) & m)) & m
        g = (g + h) & m
        return a, b, c, d, e, f, g, h

    def random(self) -> int:
        result = self.seed[self.rand_cnt]
        if self.rand_cnt == 0:
            self._isaac64()
            self.rand_cnt = 255
        else:
            self.rand_cnt -= 1
        return result

def decrypt_channels_data(data: bytearray | bytes, key: int, enc_len: int = 131072) -> bytes:
    """Decrypt the first enc_len bytes of WeChat Channels MP4 with ISAAC64."""
    if isinstance(data, bytes):
        data = bytearray(data)
    total_len = len(data)
    actual_enc_len = min(total_len, enc_len)
    if actual_enc_len == 0 or key == 0:
        return bytes(data)

    inst = ISAAC64(key)
    for i in range(0, actual_enc_len, 8):
        rand_num = inst.random()
        keystream = struct.pack(">Q", rand_num)
        for j in range(8):
            real_idx = i + j
            if real_idx >= actual_enc_len:
                break
            data[real_idx] ^= keystream[j]
    return bytes(data)
