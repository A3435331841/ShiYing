"""微信视频号私有流媒体 ISAAC64 流密码对称解密引擎。

微信视频号分发的 MP4 多媒体流采用了定制的头部混淆加密方案：
仅对其前 128KB (131,072 字节) 数据使用 ISAAC64 伪随机流密码进行异或 (XOR) 加密，
128KB 之后的后续流数据为标准未加密的 MP4 负载。

本模块实现了 64 位 ISAAC (Indirection, Shift, Accumulate, Add, and Count) 伪随机数生成算法，
能够根据作品详情接口下发的 decode_key 种子实时复原前 128KB 密钥流并完成就地异或解密。
"""
import struct


class ISAAC64:
    """ISAAC64 快速密码学安全流密码算法核心实现。

    内部维护 256 个 64 位无符号整数的状态向量 mm 与结果向量 seed。
    每次产生 256 个 64 位随机字后触发一次状态迭代混合 (_isaac64)。
    """
    MASK64 = 0xFFFFFFFFFFFFFFFF

    def __init__(self, key: int):
        """基于指定的 64 位整数密钥初始化 ISAAC64 状态向量。

        Args:
            key: 微信视频号解密密钥（64位无符号整数）。
        """
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
    """使用 ISAAC64 流密码算法解密微信视频号 MP4 数据流的前 enc_len 字节。

    解密过程：
    1. 若未加密长度为 0 或 key 为 0，说明流未加密，直接原样返回；
    2. 使用 key 构造 ISAAC64 实例，每次取一个 64 位大端字 (8字节) 作为密钥流；
    3. 将原数据字节与密钥流逐字节执行异或 (XOR) 还原；
    4. 超过 enc_len 之后的尾部流保持原样拼接。

    Args:
        data: 视频前部分或全部字节序列。
        key: 视频号下发的 decode_key 整数解密密钥。
        enc_len: 加密区域长度（字节数），视频号固定标准为 131,072 字节 (128 KB)。

    Returns:
        解密后的无损媒体二进制字节流。
    """
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
