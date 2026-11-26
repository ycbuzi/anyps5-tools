#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_VULKAN
#include "core/App.h"
#include <android/asset_manager.h>
#include <android/log.h>
#include <android_native_app_glue.h>
#include <jni.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/system_properties.h>
#include <sys/utsname.h>
#include <unistd.h>
#include <SDL3/SDL.h>
#define SDL_MAIN_HANDLED
#include <SDL3/SDL_main.h>
#include <SDL3_image/SDL_image.h>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <vulkan/vulkan.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

namespace {

constexpr int kW = 1280;
constexpr int kH = 720;
FILE* report = nullptr;

void out(const char* fmt, ...) {
    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    __android_log_print(ANDROID_LOG_INFO, "AnyPS5OS", "%s", line);
    if (report) {
        std::fprintf(report, "%s\n", line);
        std::fflush(report);
    }
}

struct Xr {
    XrInstance instance = XR_NULL_HANDLE;
    XrSession session = XR_NULL_HANDLE;
    XrSpace space = XR_NULL_HANDLE;
    XrSwapchain swapchain = XR_NULL_HANDLE;
    XrActionSet actions = XR_NULL_HANDLE;
    XrAction stick = XR_NULL_HANDLE, thumbClick = XR_NULL_HANDLE, squeeze = XR_NULL_HANDLE, trigger = XR_NULL_HANDLE, aim = XR_NULL_HANDLE, haptic = XR_NULL_HANDLE;
    XrAction face[5] = {};
    XrSpace aimSpace[2] = {};
    XrPath hands[2] = {};
    VkInstance vk = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t family = 0;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkCommandBuffer cmd = VK_NULL_HANDLE;
    VkBuffer staging = VK_NULL_HANDLE;
    VkDeviceMemory stagingMemory = VK_NULL_HANDLE;
    void* pixels = nullptr;
    std::vector<XrSwapchainImageVulkan2KHR> images;
};

bool check(Xr& x, XrResult r, const char* what) {
    if (XR_SUCCEEDED(r)) return true;
    char buf[XR_MAX_RESULT_STRING_SIZE];
    if (x.instance == XR_NULL_HANDLE || xrResultToString(x.instance, r, buf) != XR_SUCCESS) std::snprintf(buf, sizeof buf, "%d", r);
    out("FAIL %s -> %s", what, buf);
    return false;
}

void makeDirs(const std::string& path) {
    for (size_t i = 1; i < path.size(); i++)
        if (path[i] == '/') mkdir(path.substr(0, i).c_str(), 0755);
}

std::string readAsset(AAssetManager* am, const char* name) {
    AAsset* a = AAssetManager_open(am, name, AASSET_MODE_STREAMING);
    if (!a) {
        out("asset missing: %s", name);
        return {};
    }
    std::string s;
    char buf[65536];
    int n;
    while ((n = AAsset_read(a, buf, sizeof buf)) > 0) s.append(buf, n);
    AAsset_close(a);
    return s;
}

int extractAssets(AAssetManager* am, const std::string& root) {
    std::istringstream list(readAsset(am, "files.txt"));
    std::string rel;
    int n = 0;
    while (std::getline(list, rel)) {
        if (rel.empty()) continue;
        std::string data = readAsset(am, rel.c_str());
        std::string path = root + "/" + rel;
        makeDirs(path);
        std::ofstream f(path, std::ios::binary | std::ios::trunc);
        f.write(data.data(), (std::streamsize)data.size());
        if (f) n++;
    }
    return n;
}

std::string readFile(const std::string& path) {
    std::ifstream f(path);
    std::string s;
    std::getline(f, s);
    while (!s.empty() && (s.back() == '\r' || s.back() == ' ')) s.pop_back();
    return s;
}

bool initVulkan(Xr& x, XrSystemId system) {
    PFN_xrGetVulkanGraphicsRequirements2KHR getReq = nullptr;
    PFN_xrCreateVulkanInstanceKHR createInstance = nullptr;
    PFN_xrGetVulkanGraphicsDevice2KHR getDevice = nullptr;
    PFN_xrCreateVulkanDeviceKHR createDevice = nullptr;
    if (!check(x, xrGetInstanceProcAddr(x.instance, "xrGetVulkanGraphicsRequirements2KHR", (PFN_xrVoidFunction*)&getReq), "requirements")) return false;
    if (!check(x, xrGetInstanceProcAddr(x.instance, "xrCreateVulkanInstanceKHR", (PFN_xrVoidFunction*)&createInstance), "create instance")) return false;
    if (!check(x, xrGetInstanceProcAddr(x.instance, "xrGetVulkanGraphicsDevice2KHR", (PFN_xrVoidFunction*)&getDevice), "device")) return false;
    if (!check(x, xrGetInstanceProcAddr(x.instance, "xrCreateVulkanDeviceKHR", (PFN_xrVoidFunction*)&createDevice), "create device")) return false;
    XrGraphicsRequirementsVulkan2KHR req{XR_TYPE_GRAPHICS_REQUIREMENTS_VULKAN2_KHR};
    if (!check(x, getReq(x.instance, system, &req), "graphics requirements")) return false;

    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "AnyPS5 OS";
    app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    XrVulkanInstanceCreateInfoKHR xici{XR_TYPE_VULKAN_INSTANCE_CREATE_INFO_KHR};
    xici.systemId = system;
    xici.pfnGetInstanceProcAddr = vkGetInstanceProcAddr;
    xici.vulkanCreateInfo = &ici;
    VkResult vr = VK_SUCCESS;
    if (!check(x, createInstance(x.instance, &xici, &x.vk, &vr), "vulkan instance") || vr != VK_SUCCESS) return false;
    XrVulkanGraphicsDeviceGetInfoKHR gdi{XR_TYPE_VULKAN_GRAPHICS_DEVICE_GET_INFO_KHR};
    gdi.systemId = system;
    gdi.vulkanInstance = x.vk;
    if (!check(x, getDevice(x.instance, &gdi, &x.phys), "physical device")) return false;

    uint32_t count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(x.phys, &count, nullptr);
    std::vector<VkQueueFamilyProperties> families(count);
    vkGetPhysicalDeviceQueueFamilyProperties(x.phys, &count, families.data());
    for (uint32_t i = 0; i < count; i++)
        if (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
            x.family = i;
            break;
        }
    float priority = 1.f;
    VkDeviceQueueCreateInfo qci{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = x.family;
    qci.queueCount = 1;
    qci.pQueuePriorities = &priority;
    VkDeviceCreateInfo dci{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qci;
    XrVulkanDeviceCreateInfoKHR xdci{XR_TYPE_VULKAN_DEVICE_CREATE_INFO_KHR};
    xdci.systemId = system;
    xdci.pfnGetInstanceProcAddr = vkGetInstanceProcAddr;
    xdci.vulkanPhysicalDevice = x.phys;
    xdci.vulkanCreateInfo = &dci;
    if (!check(x, createDevice(x.instance, &xdci, &x.device, &vr), "vulkan device") || vr != VK_SUCCESS) return false;
    vkGetDeviceQueue(x.device, x.family, 0, &x.queue);

    VkCommandPoolCreateInfo cpci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    cpci.queueFamilyIndex = x.family;
    vkCreateCommandPool(x.device, &cpci, nullptr, &x.pool);
    VkCommandBufferAllocateInfo cbai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cbai.commandPool = x.pool;
    cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbai.commandBufferCount = 1;
    vkAllocateCommandBuffers(x.device, &cbai, &x.cmd);

    VkBufferCreateInfo bci{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bci.size = (VkDeviceSize)kW * kH * 4;
    bci.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
    if (vkCreateBuffer(x.device, &bci, nullptr, &x.staging) != VK_SUCCESS) return false;
    VkMemoryRequirements mr;
    vkGetBufferMemoryRequirements(x.device, x.staging, &mr);
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(x.phys, &mp);
    const VkMemoryPropertyFlags want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    uint32_t type = UINT32_MAX;
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((mr.memoryTypeBits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want) {
            type = i;
            break;
        }
    if (type == UINT32_MAX) return false;
    VkMemoryAllocateInfo mai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    mai.allocationSize = mr.size;
    mai.memoryTypeIndex = type;
    if (vkAllocateMemory(x.device, &mai, nullptr, &x.stagingMemory) != VK_SUCCESS) return false;
    vkBindBufferMemory(x.device, x.staging, x.stagingMemory, 0);
    return vkMapMemory(x.device, x.stagingMemory, 0, VK_WHOLE_SIZE, 0, &x.pixels) == VK_SUCCESS;
}

XrPath path(Xr& x, const char* s) {
    XrPath p = XR_NULL_PATH;
    xrStringToPath(x.instance, s, &p);
    return p;
}

bool initActions(Xr& x) {
    XrActionSetCreateInfo asci{XR_TYPE_ACTION_SET_CREATE_INFO};
    std::strcpy(asci.actionSetName, "console");
    std::strcpy(asci.localizedActionSetName, "Console");
    if (!check(x, xrCreateActionSet(x.instance, &asci, &x.actions), "action set")) return false;
    x.hands[0] = path(x, "/user/hand/left");
    x.hands[1] = path(x, "/user/hand/right");
    auto make = [&](const char* name, XrActionType type, XrAction* a, bool perHand) {
        XrActionCreateInfo aci{XR_TYPE_ACTION_CREATE_INFO};
        std::strcpy(aci.actionName, name);
        std::strcpy(aci.localizedActionName, name);
        aci.actionType = type;
        aci.countSubactionPaths = perHand ? 2 : 0;
        aci.subactionPaths = perHand ? x.hands : nullptr;
        return check(x, xrCreateAction(x.actions, &aci, a), name);
    };
    if (!make("stick", XR_ACTION_TYPE_VECTOR2F_INPUT, &x.stick, true) || !make("stick_click", XR_ACTION_TYPE_BOOLEAN_INPUT, &x.thumbClick, true) ||
        !make("grip", XR_ACTION_TYPE_FLOAT_INPUT, &x.squeeze, true) || !make("trigger", XR_ACTION_TYPE_FLOAT_INPUT, &x.trigger, true) ||
        !make("aim", XR_ACTION_TYPE_POSE_INPUT, &x.aim, true) || !make("haptic", XR_ACTION_TYPE_VIBRATION_OUTPUT, &x.haptic, true))
        return false;
    const char* faceNames[5] = {"button_a", "button_b", "button_x", "button_y", "menu"};
    for (int i = 0; i < 5; i++)
        if (!make(faceNames[i], XR_ACTION_TYPE_BOOLEAN_INPUT, &x.face[i], false)) return false;
    std::vector<XrActionSuggestedBinding> b = {
        {x.face[0], path(x, "/user/hand/right/input/a/click")},
        {x.face[1], path(x, "/user/hand/right/input/b/click")},
        {x.face[2], path(x, "/user/hand/left/input/x/click")},
        {x.face[3], path(x, "/user/hand/left/input/y/click")},
        {x.face[4], path(x, "/user/hand/left/input/menu/click")},
    };
    const char* side[2] = {"/user/hand/left", "/user/hand/right"};
    for (int h = 0; h < 2; h++) {
        std::string s = side[h];
        b.push_back({x.stick, path(x, (s + "/input/thumbstick").c_str())});
        b.push_back({x.thumbClick, path(x, (s + "/input/thumbstick/click").c_str())});
        b.push_back({x.squeeze, path(x, (s + "/input/squeeze/value").c_str())});
        b.push_back({x.trigger, path(x, (s + "/input/trigger/value").c_str())});
        b.push_back({x.aim, path(x, (s + "/input/aim/pose").c_str())});
        b.push_back({x.haptic, path(x, (s + "/output/haptic").c_str())});
    }
    XrInteractionProfileSuggestedBinding sb{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
    sb.interactionProfile = path(x, "/interaction_profiles/oculus/touch_controller");
    sb.countSuggestedBindings = (uint32_t)b.size();
    sb.suggestedBindings = b.data();
    if (!check(x, xrSuggestInteractionProfileBindings(x.instance, &sb), "bindings")) return false;
    for (int h = 0; h < 2; h++) {
        XrActionSpaceCreateInfo asi{XR_TYPE_ACTION_SPACE_CREATE_INFO};
        asi.action = x.aim;
        asi.subactionPath = x.hands[h];
        asi.poseInActionSpace.orientation.w = 1;
        if (!check(x, xrCreateActionSpace(x.session, &asi, &x.aimSpace[h]), "aim space")) return false;
    }
    XrSessionActionSetsAttachInfo ai{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};
    ai.countActionSets = 1;
    ai.actionSets = &x.actions;
    return check(x, xrAttachSessionActionSets(x.session, &ai), "attach actions");
}


struct Panel {
    float distance = 2.0f;
    float width = 2.4f;
};

struct Hand {
    bool trigger = false, grip = false, click = false, onScreen = false;
    float px = 0, py = 0;
};

struct Pad {
    bool face[5] = {};
    Hand hand[2];
    int pointer = 1;
    double menuSince = -1;
    bool menuFired = false;
    int dir = -1;
    double nextRepeat = 0;
    float lastPx = -1, lastPy = -1;
};

float floatState(Xr& x, XrAction a, int h) {
    XrActionStateGetInfo gi{XR_TYPE_ACTION_STATE_GET_INFO};
    gi.action = a;
    gi.subactionPath = x.hands[h];
    XrActionStateFloat st{XR_TYPE_ACTION_STATE_FLOAT};
    return XR_SUCCEEDED(xrGetActionStateFloat(x.session, &gi, &st)) && st.isActive ? st.currentState : 0.f;
}

bool boolState(Xr& x, XrAction a, XrPath sub) {
    XrActionStateGetInfo gi{XR_TYPE_ACTION_STATE_GET_INFO};
    gi.action = a;
    gi.subactionPath = sub;
    XrActionStateBoolean st{XR_TYPE_ACTION_STATE_BOOLEAN};
    return XR_SUCCEEDED(xrGetActionStateBoolean(x.session, &gi, &st)) && st.isActive && st.currentState;
}

bool pressed(bool& held, float value) {
    bool was = held;
    held = was ? value > 0.55f : value > 0.75f;
    return held && !was;
}

void buzz(Xr& x, int h) {
    XrHapticActionInfo hi{XR_TYPE_HAPTIC_ACTION_INFO};
    hi.action = x.haptic;
    hi.subactionPath = x.hands[h];
    XrHapticVibration v{XR_TYPE_HAPTIC_VIBRATION};
    v.duration = 15000000;
    v.frequency = XR_FREQUENCY_UNSPECIFIED;
    v.amplitude = 0.35f;
    xrApplyHapticFeedback(x.session, &hi, (const XrHapticBaseHeader*)&v);
}

void aimAt(Xr& x, int h, XrTime when, const Panel& panel, Hand& hand) {
    hand.onScreen = false;
    XrSpaceLocation loc{XR_TYPE_SPACE_LOCATION};
    if (XR_FAILED(xrLocateSpace(x.aimSpace[h], x.space, when, &loc))) return;
    const XrSpaceLocationFlags need = XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT;
    if ((loc.locationFlags & need) != need) return;
    const XrQuaternionf& q = loc.pose.orientation;
    float dx = -(2 * (q.x * q.z + q.w * q.y));
    float dy = -(2 * (q.y * q.z - q.w * q.x));
    float dz = -(1 - 2 * (q.x * q.x + q.y * q.y));
    if (dz > -1e-3f) return;
    float t = (-panel.distance - loc.pose.position.z) / dz;
    if (t <= 0) return;
    float hx = loc.pose.position.x + dx * t, hy = loc.pose.position.y + dy * t;
    float height = panel.width * kH / kW;
    float u = hx / panel.width + 0.5f, v = 0.5f - hy / height;
    if (u < 0 || u > 1 || v < 0 || v > 1) return;
    hand.onScreen = true;
    hand.px = u * kW;
    hand.py = v * kH;
}

void readInput(Xr& x, Pad& pad, double now, XrTime when, const Panel& panel, std::vector<ui::Action>& acts) {
    XrActiveActionSet active{x.actions, XR_NULL_PATH};
    XrActionsSyncInfo si{XR_TYPE_ACTIONS_SYNC_INFO};
    si.countActiveActionSets = 1;
    si.activeActionSets = &active;
    if (XR_FAILED(xrSyncActions(x.session, &si))) return;
    const char* faceActs[4] = {"ok", "back", "square", "triangle"};
    for (int i = 0; i < 4; i++) {
        bool down = boolState(x, x.face[i], XR_NULL_PATH);
        if (down && !pad.face[i]) acts.push_back({faceActs[i], "", false, true});
        pad.face[i] = down;
    }
    bool menu = boolState(x, x.face[4], XR_NULL_PATH);
    if (menu && !pad.face[4]) {
        pad.menuSince = now;
        pad.menuFired = false;
    } else if (menu && !pad.menuFired && now - pad.menuSince >= 0.6) {
        pad.menuFired = true;
        acts.push_back({"ps", "", false, true});
    } else if (!menu && pad.face[4] && !pad.menuFired) {
        acts.push_back({"options", "", false, true});
    }
    pad.face[4] = menu;

    const char* gripActs[2] = {"l1", "r1"};
    const char* triggerActs[2] = {"l2", "r2"};
    const char* clickActs[2] = {"l3", "r3"};
    for (int h = 0; h < 2; h++) {
        Hand& hd = pad.hand[h];
        aimAt(x, h, when, panel, hd);
        if (pressed(hd.grip, floatState(x, x.squeeze, h))) acts.push_back({gripActs[h], "", false, true});
        bool click = boolState(x, x.thumbClick, x.hands[h]);
        if (click && !hd.click) acts.push_back({clickActs[h], "", false, true});
        hd.click = click;
        if (pressed(hd.trigger, floatState(x, x.trigger, h))) {
            if (hd.onScreen) {
                pad.pointer = h;
                acts.push_back({"pointer.click", std::to_string(hd.px) + "," + std::to_string(hd.py)});
                buzz(x, h);
            } else {
                acts.push_back({triggerActs[h], "", false, true});
            }
        }
    }
    if (!pad.hand[pad.pointer].onScreen && pad.hand[1 - pad.pointer].onScreen) pad.pointer = 1 - pad.pointer;
    const Hand& ph = pad.hand[pad.pointer];
    if (ph.onScreen && (std::fabs(ph.px - pad.lastPx) > 0.5f || std::fabs(ph.py - pad.lastPy) > 0.5f)) {
        pad.lastPx = ph.px;
        pad.lastPy = ph.py;
        acts.push_back({"pointer.move", std::to_string(ph.px) + "," + std::to_string(ph.py)});
    }

    float ax = 0, ay = 0;
    for (int h = 0; h < 2; h++) {
        XrActionStateGetInfo gi{XR_TYPE_ACTION_STATE_GET_INFO};
        gi.action = x.stick;
        gi.subactionPath = x.hands[h];
        XrActionStateVector2f v{XR_TYPE_ACTION_STATE_VECTOR2F};
        if (XR_SUCCEEDED(xrGetActionStateVector2f(x.session, &gi, &v)) && v.isActive &&
            std::fabs(v.currentState.x) + std::fabs(v.currentState.y) > std::fabs(ax) + std::fabs(ay)) {
            ax = v.currentState.x;
            ay = v.currentState.y;
        }
    }
    int dir = -1;
    float hold = pad.dir >= 0 ? 0.4f : 0.6f;
    if (std::fabs(ax) > hold || std::fabs(ay) > hold) dir = std::fabs(ax) > std::fabs(ay) ? (ax > 0 ? 3 : 2) : (ay > 0 ? 0 : 1);
    const char* dirs[4] = {"up", "down", "left", "right"};
    if (dir != pad.dir) {
        pad.dir = dir;
        pad.nextRepeat = now + 0.4;
        if (dir >= 0) acts.push_back({dirs[dir], "", false, true});
    } else if (dir >= 0 && now >= pad.nextRepeat) {
        pad.nextRepeat = now + 0.12;
        acts.push_back({dirs[dir], "", true, true});
    }
}

void reportControllers(Xr& x, ui::App& console) {
    nlohmann::json pads = nlohmann::json::array();
    const char* names[2] = {"Touch controller (left)", "Touch controller (right)"};
    for (int h = 0; h < 2; h++) {
        XrInteractionProfileState st{XR_TYPE_INTERACTION_PROFILE_STATE};
        if (XR_SUCCEEDED(xrGetCurrentInteractionProfile(x.session, x.hands[h], &st)) && st.interactionProfile != XR_NULL_PATH) pads.push_back(names[h]);
    }
    console.H["pads"] = pads;
}

Panel panelFromSettings(ui::App& console) {
    static const float widths[] = {1.6f, 2.4f, 3.2f, 4.4f};
    static const float distances[] = {1.4f, 2.0f, 3.0f};
    auto pick = [&](const char* k, int d, int hi) {
        nlohmann::json v = console.settings.get(k, d);
        int n = v.is_number_integer() ? v.get<int>() : d;
        return n >= 1 && n <= hi ? n : d;
    };
    Panel p;
    p.width = widths[pick("vrSize", 2, 4) - 1];
    p.distance = distances[pick("vrDistance", 2, 3) - 1];
    return p;
}

std::string prop(const char* name) {
    char buf[PROP_VALUE_MAX] = {};
    __system_property_get(name, buf);
    return buf;
}

std::string writeQuestState(const std::string& internal) {
    nlohmann::json base = nlohmann::json::parse(readFile(internal + "/fixture.json"), nullptr, false);
    nlohmann::json settings = base.is_object() && base.contains("state") ? base["state"].value("settings", nlohmann::json::object()) : nlohmann::json::object();
    struct utsname u{};
    uname(&u);
    std::string model = prop("ro.product.model"), maker = prop("ro.product.manufacturer"), soc = prop("ro.soc.model");
    std::string machine = (maker.empty() ? "" : maker + " ") + (model.empty() ? "Quest" : model) + (soc.empty() ? "" : " (" + soc + ")");
    long pages = sysconf(_SC_PHYS_PAGES), pageSize = sysconf(_SC_PAGESIZE);
    nlohmann::json drives = nlohmann::json::array();
    struct statvfs fs{};
    if (statvfs("/sdcard", &fs) == 0)
        drives.push_back({{"name", "/sdcard"}, {"label", "Headset storage"}, {"free", (double)fs.f_bavail * fs.f_frsize}, {"total", (double)fs.f_blocks * fs.f_frsize}});
    char now[32];
    std::time_t t = std::time(nullptr);
    std::strftime(now, sizeof now, "%Y-%m-%dT%H:%M:%S", std::localtime(&t));
    nlohmann::json info = {{"os", "Horizon OS, Android " + prop("ro.build.version.release") + " (" + prop("ro.build.display.id") + ")"},
                           {"machine", machine + ", " + std::to_string((long long)((double)pages * pageSize / 1e9 + 0.5)) + " GB memory"},
                           {"kernel", std::string(u.sysname) + " " + u.release + " " + u.machine}};
    nlohmann::json state = {{"host", true}, {"platform", "quest"}, {"device", "quest"}, {"paused", false}, {"pads", nlohmann::json::array()},
                            {"build", "Quest app, " __DATE__}, {"program", "AnyPS5 OS"}, {"root", ""}, {"playing", nullptr}, {"settings", settings},
                            {"drives", drives}, {"areas", nlohmann::json::array()}};
    nlohmann::json doc = {{"now", now}, {"state", state}, {"library", nlohmann::json::array()}, {"media", {{"items", nlohmann::json::array()}}},
                          {"sys", {{"platform.info", {{"ok", true}, {"data", info}}}}}, {"events", nlohmann::json::array()}};
    std::string path = internal + "/quest-state.json";
    std::ofstream(path, std::ios::trunc) << doc.dump(1);
    out("quest state: %s, %s", info["machine"].get<std::string>().c_str(), info["os"].get<std::string>().c_str());
    return path;
}

void upload(Xr& x, VkImage image) {
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    vkResetCommandBuffer(x.cmd, 0);
    vkBeginCommandBuffer(x.cmd, &bi);
    VkImageSubresourceRange range{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    b.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    b.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange = range;
    vkCmdPipelineBarrier(x.cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &b);
    VkBufferImageCopy copy{};
    copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    copy.imageExtent = {(uint32_t)kW, (uint32_t)kH, 1};
    vkCmdCopyBufferToImage(x.cmd, x.staging, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    b.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    b.dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_READ_BIT;
    b.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    b.newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    vkCmdPipelineBarrier(x.cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, 0, nullptr, 0, nullptr, 1, &b);
    vkEndCommandBuffer(x.cmd);
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1;
    si.pCommandBuffers = &x.cmd;
    vkQueueSubmit(x.queue, 1, &si, VK_NULL_HANDLE);
    vkQueueWaitIdle(x.queue);
}

void scripted(const std::string& dir, double now, std::vector<std::string>& script, double& nextKey, std::vector<ui::Action>& acts) {
    std::string file = dir + "/keys";
    if (script.empty() && access(file.c_str(), F_OK) == 0) {
        std::ifstream f(file);
        std::string k;
        while (f >> k) script.push_back(k);
        unlink(file.c_str());
        out("key script: %zu keys", script.size());
        nextKey = now;
    }
    if (script.empty() || now < nextKey) return;
    std::string k = script.front();
    script.erase(script.begin());
    if (k.rfind("wait:", 0) == 0) nextKey = now + std::atof(k.c_str() + 5);
    else if (k.rfind("click:", 0) == 0 || k.rfind("move:", 0) == 0) {
        bool click = k[0] == 'c';
        acts.push_back({click ? "pointer.click" : "pointer.move", k.substr(click ? 6 : 5)});
        nextKey = now + 0.6;
    } else {
        acts.push_back({k, "", false, true});
        nextKey = now + 0.6;
    }
}

int appState = 1;

void onCmd(android_app*, int32_t cmd) {
    if (cmd == APP_CMD_RESUME) appState = 1;
    if (cmd == APP_CMD_PAUSE) appState = 0;
    if (cmd == APP_CMD_DESTROY) appState = -1;
}

void run(android_app* app, Xr& x) {
    std::string internal = app->activity->internalDataPath ? app->activity->internalDataPath : "/data/local/tmp";
    std::string external = app->activity->externalDataPath ? app->activity->externalDataPath : internal;
    out("assets extracted: %d files", extractAssets(app->activity->assetManager, internal));
    setenv("ANYPS5_FONT_DIR", (internal + "/fonts/").c_str(), 1);
    for (const char* f : {"NotoSans-Light.ttf", "NotoSans-Regular.ttf", "NotoSans-Bold.ttf"}) {
        struct stat st{};
        out("font %s: %lld bytes", f, stat((internal + "/fonts/" + f).c_str(), &st) == 0 ? (long long)st.st_size : -1LL);
    }

    PFN_xrInitializeLoaderKHR initLoader = nullptr;
    xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR", (PFN_xrVoidFunction*)&initLoader);
    if (initLoader) {
        XrLoaderInitInfoAndroidKHR li{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
        li.applicationVM = app->activity->vm;
        li.applicationContext = app->activity->clazz;
        initLoader((const XrLoaderInitInfoBaseHeaderKHR*)&li);
    }
    const char* exts[] = {XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME, XR_KHR_VULKAN_ENABLE2_EXTENSION_NAME};
    XrInstanceCreateInfoAndroidKHR android{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
    android.applicationVM = app->activity->vm;
    android.applicationActivity = app->activity->clazz;
    XrInstanceCreateInfo ici{XR_TYPE_INSTANCE_CREATE_INFO};
    ici.next = &android;
    std::strcpy(ici.applicationInfo.applicationName, "AnyPS5 OS");
    ici.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    ici.enabledExtensionCount = 2;
    ici.enabledExtensionNames = exts;
    if (!check(x, xrCreateInstance(&ici, &x.instance), "instance")) return;
    XrSystemGetInfo sgi{XR_TYPE_SYSTEM_GET_INFO};
    sgi.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system = XR_NULL_SYSTEM_ID;
    if (!check(x, xrGetSystem(x.instance, &sgi, &system), "system")) return;
    if (!initVulkan(x, system)) {
        out("FAIL vulkan setup");
        return;
    }
    XrGraphicsBindingVulkan2KHR binding{XR_TYPE_GRAPHICS_BINDING_VULKAN2_KHR};
    binding.instance = x.vk;
    binding.physicalDevice = x.phys;
    binding.device = x.device;
    binding.queueFamilyIndex = x.family;
    XrSessionCreateInfo sci{XR_TYPE_SESSION_CREATE_INFO};
    sci.next = &binding;
    sci.systemId = system;
    if (!check(x, xrCreateSession(x.instance, &sci, &x.session), "session")) return;
    XrReferenceSpaceCreateInfo rsci{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    rsci.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
    rsci.poseInReferenceSpace.orientation.w = 1;
    if (!check(x, xrCreateReferenceSpace(x.session, &rsci, &x.space), "space")) return;
    XrSwapchainCreateInfo swci{XR_TYPE_SWAPCHAIN_CREATE_INFO};
    swci.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT;
    swci.format = VK_FORMAT_R8G8B8A8_SRGB;
    swci.sampleCount = 1;
    swci.width = kW;
    swci.height = kH;
    swci.faceCount = 1;
    swci.arraySize = 1;
    swci.mipCount = 1;
    if (!check(x, xrCreateSwapchain(x.session, &swci, &x.swapchain), "swapchain")) return;
    uint32_t count = 0;
    xrEnumerateSwapchainImages(x.swapchain, 0, &count, nullptr);
    x.images.assign(count, XrSwapchainImageVulkan2KHR{XR_TYPE_SWAPCHAIN_IMAGE_VULKAN2_KHR});
    if (!check(x, xrEnumerateSwapchainImages(x.swapchain, count, &count, (XrSwapchainImageBaseHeader*)x.images.data()), "images")) return;
    if (!initActions(x)) return;

    SDL_Surface* surface = SDL_CreateSurfaceFrom(kW, kH, SDL_PIXELFORMAT_RGBA32, x.pixels, kW * 4);
    ui::Options o;
    o.w = kW;
    o.h = kH;
    o.gpu = true;
    o.device = "quest";
    std::string host = readFile(external + "/host.txt");
    if (!host.empty()) o.hostUrl = host;
    else o.hostFile = writeQuestState(internal);
    out("console host %s", host.empty() ? o.hostFile.c_str() : host.c_str());
    ui::App console;
    SDL_SetMainReady();
    SDL_SetAppMetadata("AnyPS5 OS", "0.1", "com.anyps5.os");
    if (!surface || !console.beginEmbedded(o, surface, internal + "/assets")) {
        out("FAIL console start: %s", SDL_GetError());
        return;
    }
    out("console started %dx%d", kW, kH);

    bool running = false;
    Pad pad;
    std::vector<std::string> script;
    double nextKey = 0;
    long frames = 0;
    double drawMs = 0;
    auto start = std::chrono::steady_clock::now();
    auto last = start;
    while (appState >= 0) {
        int events;
        android_poll_source* source;
        while (ALooper_pollOnce(running ? 0 : 100, nullptr, &events, (void**)&source) >= 0) {
            if (source) source->process(app, source);
            if (app->destroyRequested) return;
        }
        XrEventDataBuffer ev{XR_TYPE_EVENT_DATA_BUFFER};
        while (xrPollEvent(x.instance, &ev) == XR_SUCCESS) {
            if (ev.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                auto state = reinterpret_cast<XrEventDataSessionStateChanged*>(&ev)->state;
                out("session state %d", state);
                if (state == XR_SESSION_STATE_READY) {
                    XrSessionBeginInfo bi{XR_TYPE_SESSION_BEGIN_INFO};
                    bi.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                    running = check(x, xrBeginSession(x.session, &bi), "begin session");
                } else if (state == XR_SESSION_STATE_STOPPING) {
                    xrEndSession(x.session);
                    running = false;
                } else if (state == XR_SESSION_STATE_EXITING || state == XR_SESSION_STATE_LOSS_PENDING) {
                    console.endEmbedded();
                    return;
                }
            }
            ev = XrEventDataBuffer{XR_TYPE_EVENT_DATA_BUFFER};
        }
        if (!running) continue;
        XrFrameState fs{XR_TYPE_FRAME_STATE};
        if (!check(x, xrWaitFrame(x.session, nullptr, &fs), "wait frame")) break;
        if (!check(x, xrBeginFrame(x.session, nullptr), "begin frame")) break;
        auto now = std::chrono::steady_clock::now();
        double dt = std::chrono::duration<double>(now - last).count();
        last = now;
        std::vector<ui::Action> acts;
        double clock = std::chrono::duration<double>(now - start).count();
        Panel panel = panelFromSettings(console);
        readInput(x, pad, clock, fs.predictedDisplayTime, panel, acts);
        if (frames % 72 == 0) reportControllers(x, console);
        scripted(external, clock, script, nextKey, acts);
        const XrCompositionLayerBaseHeader* layers[1];
        XrCompositionLayerQuad quad{XR_TYPE_COMPOSITION_LAYER_QUAD};
        uint32_t layerCount = 0;
        if (fs.shouldRender) {
            auto t0 = std::chrono::steady_clock::now();
            console.stepEmbedded(dt, acts);
            drawMs += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            uint32_t idx = 0;
            XrSwapchainImageWaitInfo wi{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
            wi.timeout = XR_INFINITE_DURATION;
            if (check(x, xrAcquireSwapchainImage(x.swapchain, nullptr, &idx), "acquire") && check(x, xrWaitSwapchainImage(x.swapchain, &wi), "wait image")) {
                upload(x, x.images[idx].image);
                xrReleaseSwapchainImage(x.swapchain, nullptr);
                quad.space = x.space;
                quad.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
                quad.subImage.swapchain = x.swapchain;
                quad.subImage.imageRect.extent = {kW, kH};
                quad.pose.orientation.w = 1;
                quad.pose.position.z = -panel.distance;
                quad.size.width = panel.width;
                quad.size.height = panel.width * kH / kW;
                layers[0] = reinterpret_cast<const XrCompositionLayerBaseHeader*>(&quad);
                layerCount = 1;
            }
            std::string request = external + "/shot-request";
            if (frames % 36 == 0 && access(request.c_str(), F_OK) == 0) {
                unlink(request.c_str());
                std::string shot = external + "/console-shot.png";
                out("console frame saved: %s", IMG_SavePNG(surface, shot.c_str()) ? shot.c_str() : SDL_GetError());
            }
            if (++frames % 30 == 0) {
                out("frames %ld at %.0f s, console draw %.2f ms average", frames, clock, drawMs / 30);
                drawMs = 0;
            }
        }
        XrFrameEndInfo fei{XR_TYPE_FRAME_END_INFO};
        fei.displayTime = fs.predictedDisplayTime;
        fei.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        fei.layerCount = layerCount;
        fei.layers = layers;
        if (!check(x, xrEndFrame(x.session, &fei), "end frame")) break;
    }
    console.endEmbedded();
}

void cleanup(Xr& x) {
    if (x.device) vkDeviceWaitIdle(x.device);
    if (x.swapchain) xrDestroySwapchain(x.swapchain);
    if (x.space) xrDestroySpace(x.space);
    if (x.actions) xrDestroyActionSet(x.actions);
    if (x.session) xrDestroySession(x.session);
    if (x.stagingMemory) vkFreeMemory(x.device, x.stagingMemory, nullptr);
    if (x.staging) vkDestroyBuffer(x.device, x.staging, nullptr);
    if (x.pool) vkDestroyCommandPool(x.device, x.pool, nullptr);
    if (x.device) vkDestroyDevice(x.device, nullptr);
    if (x.vk) vkDestroyInstance(x.vk, nullptr);
    if (x.instance) xrDestroyInstance(x.instance);
}

}

extern "C" void android_main(android_app* app) {
    app->onAppCmd = onCmd;
    std::string dir = app->activity->externalDataPath ? app->activity->externalDataPath : app->activity->internalDataPath;
    makeDirs(dir + "/");
    report = std::fopen((dir + "/anyps5-os.log").c_str(), "w");
    Xr x;
    run(app, x);
    cleanup(x);
    out("AnyPS5 OS stopped");
    if (report) std::fclose(report);
    ANativeActivity_finish(app->activity);
    while (!app->destroyRequested) {
        int events;
        android_poll_source* source;
        if (ALooper_pollOnce(-1, nullptr, &events, (void**)&source) >= 0 && source) source->process(app, source);
    }
}
