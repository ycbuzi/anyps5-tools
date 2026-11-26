#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <linux/futex.h>
#include <stdatomic.h>

static double now_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e6 + t.tv_nsec / 1e3;
}

static int cmp_d(const void* a, const void* b) {
    double x = *(const double*)a, y = *(const double*)b;
    return x < y ? -1 : x > y;
}

#define CK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { fprintf(stderr, "%s failed %d\n", #x, r_); return 1; } } while (0)

static atomic_int fword;
static int rounds = 20000;

static void fwait(int v) { while (atomic_load(&fword) == v) syscall(SYS_futex, &fword, FUTEX_WAIT_PRIVATE, v, NULL, NULL, 0); }
static void fwake(void) { syscall(SYS_futex, &fword, FUTEX_WAKE_PRIVATE, 1, NULL, NULL, 0); }

static void* pong(void* p) {
    (void)p;
    for (int i = 0; i < rounds; i++) {
        fwait(2 * i);
        atomic_store(&fword, 2 * i + 2);
        fwake();
    }
    return NULL;
}

static double futex_rt_us(void) {
    pthread_t t;
    atomic_store(&fword, 0);
    pthread_create(&t, NULL, pong, NULL);
    double t0 = now_us();
    for (int i = 0; i < rounds; i++) {
        atomic_store(&fword, 2 * i + 1);
        fwake();
        fwait(2 * i + 1);
    }
    double t1 = now_us();
    pthread_join(t, NULL);
    return (t1 - t0) / rounds;
}

int main(int argc, char** argv) {
    int iters = argc > 1 ? atoi(argv[1]) : 1000;
    double t0 = now_us();
    volatile double sink = 0;
    for (int i = 0; i < 1000000; i++) sink += now_us();
    double clock_ns = (now_us() - t0) * 1e3 / 1000000;
    double fut = futex_rt_us();

    VkApplicationInfo ai = { VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "vkbench", 1, NULL, 0, VK_API_VERSION_1_1 };
    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &ai, 0, NULL, 0, NULL };
    VkInstance inst;
    double ti = now_us();
    CK(vkCreateInstance(&ici, NULL, &inst));
    uint32_t n = 8;
    VkPhysicalDevice pds[8];
    CK(vkEnumeratePhysicalDevices(inst, &n, pds));
    VkPhysicalDevice pd = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties pp;
    for (uint32_t i = 0; i < n; i++) {
        vkGetPhysicalDeviceProperties(pds[i], &pp);
        if (pp.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU || pp.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) { pd = pds[i]; break; }
    }
    if (!pd) { fprintf(stderr, "no GPU device\n"); return 1; }
    vkGetPhysicalDeviceProperties(pd, &pp);
    uint32_t qn = 8;
    VkQueueFamilyProperties qf[8];
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, qf);
    uint32_t qi = 0;
    while (qi < qn && !(qf[qi].queueFlags & VK_QUEUE_COMPUTE_BIT)) qi++;
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, qi, 1, &prio };
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, 0, NULL, NULL };
    VkDevice dev;
    CK(vkCreateDevice(pd, &dci, NULL, &dev));
    double init_ms = (now_us() - ti) / 1e3;
    VkQueue q;
    vkGetDeviceQueue(dev, qi, 0, &q);

    VkDeviceSize sz = 64u << 20;
    VkBufferCreateInfo bci = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, sz, VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL };
    VkBuffer buf;
    CK(vkCreateBuffer(dev, &bci, NULL, &buf));
    VkMemoryRequirements mr;
    vkGetBufferMemoryRequirements(dev, buf, &mr);
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);
    uint32_t want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, mt = 0;
    while (mt < mp.memoryTypeCount && !((mr.memoryTypeBits >> mt & 1) && (mp.memoryTypes[mt].propertyFlags & want) == want)) mt++;
    VkMemoryAllocateInfo mai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, mr.size, mt };
    VkDeviceMemory mem;
    CK(vkAllocateMemory(dev, &mai, NULL, &mem));
    CK(vkBindBufferMemory(dev, buf, mem, 0));
    void* map;
    CK(vkMapMemory(dev, mem, 0, sz, 0, &map));
    char* src = malloc(sz);
    memset(src, 0x5a, sz);
    double tm = now_us();
    for (int i = 0; i < 10; i++) { src[i] = (char)i; memcpy(map, src, sz); }
    double gbps = 10.0 * sz / ((now_us() - tm) * 1e3);

    VkCommandPoolCreateInfo cpi = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, qi };
    VkCommandPool pool;
    CK(vkCreateCommandPool(dev, &cpi, NULL, &pool));
    VkCommandBufferAllocateInfo cai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1 };
    VkCommandBuffer cb;
    CK(vkAllocateCommandBuffers(dev, &cai, &cb));
    VkCommandBufferBeginInfo cbi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, 0, NULL };
    CK(vkBeginCommandBuffer(cb, &cbi));
    vkCmdFillBuffer(cb, buf, 0, 4096, 0x12345678u);
    CK(vkEndCommandBuffer(cb));
    VkFenceCreateInfo fci = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, NULL, 0 };
    VkFence fence;
    CK(vkCreateFence(dev, &fci, NULL, &fence));
    VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL };
    double* lat = malloc(sizeof(double) * iters);
    for (int i = 0; i < iters; i++) {
        double a = now_us();
        CK(vkQueueSubmit(q, 1, &si, fence));
        CK(vkWaitForFences(dev, 1, &fence, VK_TRUE, UINT64_MAX));
        CK(vkResetFences(dev, 1, &fence));
        lat[i] = now_us() - a;
    }
    uint32_t got = ((uint32_t*)map)[0];
    qsort(lat, iters, sizeof(double), cmp_d);
    double sum = 0;
    for (int i = 0; i < iters; i++) sum += lat[i];
    printf("device=\"%s\" api=%u.%u.%u driver=0x%x\n", pp.deviceName, VK_VERSION_MAJOR(pp.apiVersion), VK_VERSION_MINOR(pp.apiVersion), VK_VERSION_PATCH(pp.apiVersion), pp.driverVersion);
    printf("init_ms=%.1f submit_wait_us avg=%.1f p50=%.1f p99=%.1f fill_ok=%d memcpy_GBps=%.2f clock_gettime_ns=%.0f futex_roundtrip_us=%.2f\n",
           init_ms, sum / iters, lat[iters / 2], lat[iters * 99 / 100], got == 0x12345678u, gbps, clock_ns, fut);
    vkDestroyFence(dev, fence, NULL);
    vkDestroyCommandPool(dev, pool, NULL);
    vkUnmapMemory(dev, mem);
    vkFreeMemory(dev, mem, NULL);
    vkDestroyBuffer(dev, buf, NULL);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return (int)(sink < 0);
}
