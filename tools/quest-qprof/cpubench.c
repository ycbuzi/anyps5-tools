typedef unsigned long u64;
typedef unsigned int u32;

static u32 table[4096];

static u64 intwork(u64 rounds)
{
    u64 h = 1469598103934665603ull;
    for (u32 i = 0; i < 4096; i++)
        table[i] = i * 2654435761u;
    for (u64 r = 0; r < rounds; r++)
        for (u32 i = 0; i < 4096; i++) {
            h ^= table[i];
            h *= 1099511628211ull;
            table[i] = (u32)(h >> 17) + table[(i * 7) & 4095];
        }
    return h;
}

static double fpwork(u64 n)
{
    double x = 1.0, y = 0.5;
    for (u64 i = 0; i < n; i++) {
        x = x * 1.0000001 + y;
        y = y * 0.9999999 + x * 1e-9;
    }
    return x + y;
}

static void quit(long code)
{
#if defined(__x86_64__)
    __asm__ volatile("syscall" ::"a"(60L), "D"(code) : "rcx", "r11", "memory");
#else
    register long x0 __asm__("x0") = code;
    register long x8 __asm__("x8") = 93;
    __asm__ volatile("svc #0" ::"r"(x0), "r"(x8) : "memory");
#endif
    for (;;) {
    }
}

#ifdef HOSTED
int main(void)
{
    u64 a = intwork(6000);
    double b = fpwork(60000000ull);
    return (int)((a ^ (u64)b) & 63);
}
#else
void _start(void)
{
    u64 a = intwork(6000);
    double b = fpwork(60000000ull);
    quit((long)((a ^ (u64)b) & 63));
}
#endif
