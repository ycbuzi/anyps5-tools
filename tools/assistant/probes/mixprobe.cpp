#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <windows.h>
#include <cstdio>
#include <vector>
#include <cstdlib>
#include <cstring>

int main() {
    HMODULE lib = LoadLibraryA("vulkan-1.dll");
    auto gipa = (PFN_vkGetInstanceProcAddr)GetProcAddress(lib, "vkGetInstanceProcAddr");
    auto create = (PFN_vkCreateInstance)gipa(nullptr, "vkCreateInstance");
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.apiVersion = VK_API_VERSION_1_3;
    VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ci.pApplicationInfo = &app;
    VkInstance inst;
    if (create(&ci, nullptr, &inst) != VK_SUCCESS) return 1;
    auto enumDev = (PFN_vkEnumeratePhysicalDevices)gipa(inst, "vkEnumeratePhysicalDevices");
    auto props = (PFN_vkGetPhysicalDeviceProperties)gipa(inst, "vkGetPhysicalDeviceProperties");
    auto feats2 = (PFN_vkGetPhysicalDeviceFeatures2)gipa(inst, "vkGetPhysicalDeviceFeatures2");
    auto combos = (PFN_vkGetPhysicalDeviceSupportedFramebufferMixedSamplesCombinationsNV)gipa(inst, "vkGetPhysicalDeviceSupportedFramebufferMixedSamplesCombinationsNV");
    uint32_t n = 0;
    enumDev(inst, &n, nullptr);
    std::vector<VkPhysicalDevice> devs(n);
    enumDev(inst, &n, devs.data());
    for (auto d : devs) {
        VkPhysicalDeviceProperties p;
        props(d, &p);
        VkPhysicalDeviceCoverageReductionModeFeaturesNV cr{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_COVERAGE_REDUCTION_MODE_FEATURES_NV};
        VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &cr};
        feats2(d, &f);
        std::printf("%s driver %u coverageReductionMode=%u depthCounts=0x%x colorCounts=0x%x\n", p.deviceName, p.driverVersion, cr.coverageReductionMode,
                    p.limits.framebufferDepthSampleCounts, p.limits.framebufferColorSampleCounts);
        std::fflush(stdout);
        if (!combos || getenv("NOCOMBO") || !strstr(p.deviceName, getenv("ONLY") ? getenv("ONLY") : "")) continue;
        uint32_t c = 0;
        combos(d, &c, nullptr);
        std::vector<VkFramebufferMixedSamplesCombinationNV> v(c, {VK_STRUCTURE_TYPE_FRAMEBUFFER_MIXED_SAMPLES_COMBINATION_NV});
        combos(d, &c, v.data());
        for (auto& x : v)
            std::printf("  mode=%s raster=%u depthStencil=0x%x color=0x%x\n", x.coverageReductionMode == VK_COVERAGE_REDUCTION_MODE_MERGE_NV ? "merge" : "truncate",
                        x.rasterizationSamples, x.depthStencilSamples, x.colorSamples);
    }
    return 0;
}
