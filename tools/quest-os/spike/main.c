#define _GNU_SOURCE
#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_VULKAN
#include <android/log.h>
#include <android_native_app_glue.h>
#include <jni.h>
#include <sched.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#define TAG "AnyPS5Spike"
#define LOG(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

static FILE* report;

static void out(const char* fmt, ...) {
    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    LOG("%s", line);
    if (report) {
        fprintf(report, "%s\n", line);
        fflush(report);
    }
}

static void cpus(const char* when) {
    cpu_set_t set;
    CPU_ZERO(&set);
    char list[64] = "";
    if (sched_getaffinity(0, sizeof set, &set) == 0) {
        for (int i = 0; i < 16; i++)
            if (CPU_ISSET(i, &set)) {
                char n[8];
                snprintf(n, sizeof n, "%d ", i);
                strcat(list, n);
            }
    }
    char cpuset[128] = "?";
    FILE* f = fopen("/proc/self/cpuset", "r");
    if (f) {
        if (fgets(cpuset, sizeof cpuset, f)) cpuset[strcspn(cpuset, "\n")] = 0;
        fclose(f);
    }
    out("cpus %s: affinity [%s] cpuset %s", when, list, cpuset);
}

static const char* xrResult(XrInstance inst, XrResult r) {
    static char buf[XR_MAX_RESULT_STRING_SIZE];
    if (inst != XR_NULL_HANDLE && xrResultToString(inst, r, buf) == XR_SUCCESS) return buf;
    snprintf(buf, sizeof buf, "%d", r);
    return buf;
}

#define XR(call)                                                         \
    do {                                                                 \
        XrResult r_ = (call);                                            \
        if (XR_FAILED(r_)) {                                             \
            out("FAIL %s -> %s", #call, xrResult(instance, r_));         \
            goto done;                                                   \
        }                                                                \
    } while (0)

static int appState = 1;

static void onCmd(struct android_app* app, int32_t cmd) {
    if (cmd == APP_CMD_RESUME) appState = 1;
    if (cmd == APP_CMD_PAUSE) appState = 0;
    if (cmd == APP_CMD_DESTROY) appState = -1;
}

void android_main(struct android_app* app) {
    app->onAppCmd = onCmd;
    char path[512];
    snprintf(path, sizeof path, "%s/spike-report.txt", app->activity->externalDataPath ? app->activity->externalDataPath : app->activity->internalDataPath);
    report = fopen(path, "w");
    out("report %s", path);
    cpus("at start");

    XrInstance instance = XR_NULL_HANDLE;
    XrSession session = XR_NULL_HANDLE;
    XrSwapchain swapchain = XR_NULL_HANDLE;
    XrSpace space = XR_NULL_HANDLE;
    VkInstance vkInstance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;

    PFN_xrInitializeLoaderKHR initLoader = NULL;
    xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR", (PFN_xrVoidFunction*)&initLoader);
    if (initLoader) {
        XrLoaderInitInfoAndroidKHR li = {XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
        li.applicationVM = app->activity->vm;
        li.applicationContext = app->activity->clazz;
        initLoader((const XrLoaderInitInfoBaseHeaderKHR*)&li);
    }

    const char* exts[] = {XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME, XR_KHR_VULKAN_ENABLE2_EXTENSION_NAME};
    XrInstanceCreateInfoAndroidKHR android = {XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
    android.applicationVM = app->activity->vm;
    android.applicationActivity = app->activity->clazz;
    XrInstanceCreateInfo ici = {XR_TYPE_INSTANCE_CREATE_INFO};
    ici.next = &android;
    strcpy(ici.applicationInfo.applicationName, "AnyPS5 OS spike");
    ici.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    ici.enabledExtensionCount = 2;
    ici.enabledExtensionNames = exts;
    XR(xrCreateInstance(&ici, &instance));
    XrInstanceProperties ip = {XR_TYPE_INSTANCE_PROPERTIES};
    xrGetInstanceProperties(instance, &ip);
    out("runtime %s %u.%u.%u", ip.runtimeName, XR_VERSION_MAJOR(ip.runtimeVersion), XR_VERSION_MINOR(ip.runtimeVersion), XR_VERSION_PATCH(ip.runtimeVersion));

    XrSystemGetInfo sgi = {XR_TYPE_SYSTEM_GET_INFO};
    sgi.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system;
    XR(xrGetSystem(instance, &sgi, &system));
    XrSystemProperties sp = {XR_TYPE_SYSTEM_PROPERTIES};
    xrGetSystemProperties(instance, system, &sp);
    out("system %s, max layers %u", sp.systemName, sp.graphicsProperties.maxLayerCount);

    PFN_xrGetVulkanGraphicsRequirements2KHR getReq;
    PFN_xrCreateVulkanInstanceKHR createVkInstance;
    PFN_xrGetVulkanGraphicsDevice2KHR getVkDevice;
    PFN_xrCreateVulkanDeviceKHR createVkDevice;
    XR(xrGetInstanceProcAddr(instance, "xrGetVulkanGraphicsRequirements2KHR", (PFN_xrVoidFunction*)&getReq));
    XR(xrGetInstanceProcAddr(instance, "xrCreateVulkanInstanceKHR", (PFN_xrVoidFunction*)&createVkInstance));
    XR(xrGetInstanceProcAddr(instance, "xrGetVulkanGraphicsDevice2KHR", (PFN_xrVoidFunction*)&getVkDevice));
    XR(xrGetInstanceProcAddr(instance, "xrCreateVulkanDeviceKHR", (PFN_xrVoidFunction*)&createVkDevice));
    XrGraphicsRequirementsVulkan2KHR req = {XR_TYPE_GRAPHICS_REQUIREMENTS_VULKAN2_KHR};
    XR(getReq(instance, system, &req));

    VkApplicationInfo vai = {VK_STRUCTURE_TYPE_APPLICATION_INFO};
    vai.pApplicationName = "AnyPS5 OS spike";
    vai.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo vici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    vici.pApplicationInfo = &vai;
    XrVulkanInstanceCreateInfoKHR xvici = {XR_TYPE_VULKAN_INSTANCE_CREATE_INFO_KHR};
    xvici.systemId = system;
    xvici.pfnGetInstanceProcAddr = vkGetInstanceProcAddr;
    xvici.vulkanCreateInfo = &vici;
    VkResult vr;
    XR(createVkInstance(instance, &xvici, &vkInstance, &vr));
    if (vr != VK_SUCCESS) { out("FAIL vkCreateInstance %d", vr); goto done; }

    XrVulkanGraphicsDeviceGetInfoKHR gdi = {XR_TYPE_VULKAN_GRAPHICS_DEVICE_GET_INFO_KHR};
    gdi.systemId = system;
    gdi.vulkanInstance = vkInstance;
    VkPhysicalDevice phys;
    XR(getVkDevice(instance, &gdi, &phys));

    VkPhysicalDeviceDriverProperties driver = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
    VkPhysicalDeviceSubgroupProperties subgroup = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES};
    subgroup.pNext = &driver;
    VkPhysicalDeviceProperties2 props = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
    props.pNext = &subgroup;
    vkGetPhysicalDeviceProperties2(phys, &props);
    VkPhysicalDevice8BitStorageFeatures eight = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES};
    VkPhysicalDeviceFeatures2 feats = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
    feats.pNext = &eight;
    vkGetPhysicalDeviceFeatures2(phys, &feats);
    out("gpu %s, api %u.%u.%u, driver %s %s", props.properties.deviceName, VK_VERSION_MAJOR(props.properties.apiVersion),
        VK_VERSION_MINOR(props.properties.apiVersion), VK_VERSION_PATCH(props.properties.apiVersion), driver.driverName, driver.driverInfo);
    out("subgroup size %u, stages 0x%x (fragment %s), ops 0x%x", subgroup.subgroupSize, subgroup.supportedStages,
        (subgroup.supportedStages & VK_SHADER_STAGE_FRAGMENT_BIT) ? "yes" : "no", subgroup.supportedOperations);
    out("storageBuffer8BitAccess %u, uniformAndStorageBuffer8BitAccess %u, shaderInt64 %u, geometryShader %u",
        eight.storageBuffer8BitAccess, eight.uniformAndStorageBuffer8BitAccess, feats.features.shaderInt64, feats.features.geometryShader);

    uint32_t qfCount = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &qfCount, NULL);
    VkQueueFamilyProperties qf[16];
    if (qfCount > 16) qfCount = 16;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &qfCount, qf);
    uint32_t family = 0;
    for (uint32_t i = 0; i < qfCount; i++)
        if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { family = i; break; }
    float prio = 1.f;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = family;
    qci.queueCount = 1;
    qci.pQueuePriorities = &prio;
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qci;
    XrVulkanDeviceCreateInfoKHR xdci = {XR_TYPE_VULKAN_DEVICE_CREATE_INFO_KHR};
    xdci.systemId = system;
    xdci.pfnGetInstanceProcAddr = vkGetInstanceProcAddr;
    xdci.vulkanPhysicalDevice = phys;
    xdci.vulkanCreateInfo = &dci;
    XR(createVkDevice(instance, &xdci, &device, &vr));
    if (vr != VK_SUCCESS) { out("FAIL vkCreateDevice %d", vr); goto done; }
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);

    XrGraphicsBindingVulkan2KHR binding = {XR_TYPE_GRAPHICS_BINDING_VULKAN2_KHR};
    binding.instance = vkInstance;
    binding.physicalDevice = phys;
    binding.device = device;
    binding.queueFamilyIndex = family;
    binding.queueIndex = 0;
    XrSessionCreateInfo sci = {XR_TYPE_SESSION_CREATE_INFO};
    sci.next = &binding;
    sci.systemId = system;
    XR(xrCreateSession(instance, &sci, &session));
    out("session created");

    XrReferenceSpaceCreateInfo rsci = {XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    rsci.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
    rsci.poseInReferenceSpace.orientation.w = 1;
    XR(xrCreateReferenceSpace(session, &rsci, &space));

    XrSwapchainCreateInfo swci = {XR_TYPE_SWAPCHAIN_CREATE_INFO};
    swci.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT;
    swci.format = VK_FORMAT_R8G8B8A8_SRGB;
    swci.sampleCount = 1;
    swci.width = 1024;
    swci.height = 576;
    swci.faceCount = 1;
    swci.arraySize = 1;
    swci.mipCount = 1;
    XR(xrCreateSwapchain(session, &swci, &swapchain));
    uint32_t imageCount = 0;
    xrEnumerateSwapchainImages(swapchain, 0, &imageCount, NULL);
    XrSwapchainImageVulkan2KHR images[8];
    if (imageCount > 8) imageCount = 8;
    for (uint32_t i = 0; i < imageCount; i++) images[i] = (XrSwapchainImageVulkan2KHR){XR_TYPE_SWAPCHAIN_IMAGE_VULKAN2_KHR};
    XR(xrEnumerateSwapchainImages(swapchain, imageCount, &imageCount, (XrSwapchainImageBaseHeader*)images));

    VkCommandPoolCreateInfo cpci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    cpci.queueFamilyIndex = family;
    VkCommandPool pool;
    vkCreateCommandPool(device, &cpci, NULL, &pool);
    VkCommandBufferAllocateInfo cbai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cbai.commandPool = pool;
    cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    vkAllocateCommandBuffers(device, &cbai, &cmd);

    int running = 0, frames = 0, shown = 0;
    XrSessionState state = XR_SESSION_STATE_UNKNOWN;
    time_t start = time(NULL);
    while (appState >= 0 && time(NULL) - start < 40) {
        int events;
        struct android_poll_source* source;
        while (ALooper_pollOnce(running ? 0 : 100, NULL, &events, (void**)&source) >= 0) {
            if (source) source->process(app, source);
            if (app->destroyRequested) goto done;
        }
        XrEventDataBuffer ev = {XR_TYPE_EVENT_DATA_BUFFER};
        while (xrPollEvent(instance, &ev) == XR_SUCCESS) {
            if (ev.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                state = ((XrEventDataSessionStateChanged*)&ev)->state;
                out("session state %d at %lds", state, (long)(time(NULL) - start));
                if (state == XR_SESSION_STATE_READY) {
                    XrSessionBeginInfo bi = {XR_TYPE_SESSION_BEGIN_INFO};
                    bi.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                    XR(xrBeginSession(session, &bi));
                    running = 1;
                    cpus("after begin session");
                } else if (state == XR_SESSION_STATE_FOCUSED) {
                    cpus("focused");
                } else if (state == XR_SESSION_STATE_STOPPING) {
                    xrEndSession(session);
                    running = 0;
                } else if (state == XR_SESSION_STATE_EXITING || state == XR_SESSION_STATE_LOSS_PENDING) {
                    goto done;
                }
            }
            ev = (XrEventDataBuffer){XR_TYPE_EVENT_DATA_BUFFER};
        }
        if (!running) continue;
        XrFrameState fs = {XR_TYPE_FRAME_STATE};
        XR(xrWaitFrame(session, NULL, &fs));
        XR(xrBeginFrame(session, NULL));
        const XrCompositionLayerBaseHeader* layers[1];
        XrCompositionLayerQuad quad = {XR_TYPE_COMPOSITION_LAYER_QUAD};
        uint32_t layerCount = 0;
        if (fs.shouldRender) {
            uint32_t idx;
            XR(xrAcquireSwapchainImage(swapchain, NULL, &idx));
            XrSwapchainImageWaitInfo wi = {XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
            wi.timeout = XR_INFINITE_DURATION;
            XR(xrWaitSwapchainImage(swapchain, &wi));
            VkCommandBufferBeginInfo cbbi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
            vkResetCommandBuffer(cmd, 0);
            vkBeginCommandBuffer(cmd, &cbbi);
            VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
            VkImageMemoryBarrier b = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
            b.srcAccessMask = 0;
            b.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            b.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
            b.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            b.image = images[idx].image;
            b.subresourceRange = range;
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &b);
            VkClearColorValue c = {{0.05f, 0.12f, 0.35f, 1.f}};
            vkCmdClearColorImage(cmd, images[idx].image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &c, 1, &range);
            b.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            b.dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_READ_BIT;
            b.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            b.newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
            vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, 0, NULL, 0, NULL, 1, &b);
            vkEndCommandBuffer(cmd);
            VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO};
            si.commandBufferCount = 1;
            si.pCommandBuffers = &cmd;
            vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE);
            vkQueueWaitIdle(queue);
            XR(xrReleaseSwapchainImage(swapchain, NULL));
            quad.space = space;
            quad.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
            quad.subImage.swapchain = swapchain;
            quad.subImage.imageRect.extent.width = 1024;
            quad.subImage.imageRect.extent.height = 576;
            quad.pose.orientation.w = 1;
            quad.pose.position.z = -2.5f;
            quad.size.width = 2.2f;
            quad.size.height = 2.2f * 576 / 1024;
            layers[0] = (const XrCompositionLayerBaseHeader*)&quad;
            layerCount = 1;
            shown++;
        }
        XrFrameEndInfo fei = {XR_TYPE_FRAME_END_INFO};
        fei.displayTime = fs.predictedDisplayTime;
        fei.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        fei.layerCount = layerCount;
        fei.layers = layers;
        XR(xrEndFrame(session, &fei));
        frames++;
        if (frames == 300) cpus("after 300 frames");
    }
    out("frames %d, frames with a layer shown %d, last state %d", frames, shown, state);

done:
    cpus("at end");
    if (swapchain) xrDestroySwapchain(swapchain);
    if (space) xrDestroySpace(space);
    if (session) xrDestroySession(session);
    if (device) vkDestroyDevice(device, NULL);
    if (vkInstance) vkDestroyInstance(vkInstance, NULL);
    if (instance) xrDestroyInstance(instance);
    out("spike done");
    if (report) fclose(report);
    ANativeActivity_finish(app->activity);
    while (!app->destroyRequested) {
        int events;
        struct android_poll_source* source;
        if (ALooper_pollOnce(-1, NULL, &events, (void**)&source) >= 0 && source) source->process(app, source);
    }
}
