#include <windows.h>
#include <winrt/Windows.ApplicationModel.h>
#include <winrt/Windows.ApplicationModel.Activation.h>
#include <winrt/Windows.ApplicationModel.Core.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Gaming.Input.h>
#include <winrt/Windows.Storage.h>
#include <winrt/Windows.System.h>
#include <winrt/Windows.UI.Core.h>
#include <winrt/Windows.UI.Popups.h>
#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Storage.Streams.h>
#include <winrt/Windows.Web.Http.h>
#include <filesystem>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

using namespace winrt;
using namespace winrt::Windows::ApplicationModel::Core;
using namespace winrt::Windows::UI::Core;

namespace {

std::wstring g_local;
std::wstring g_game;
std::wstring g_running;
std::mutex g_logLock;
std::atomic<bool> g_activated{false};
std::atomic<bool> g_started{false};
std::atomic<bool> g_leaving{false};
CoreDispatcher g_dispatcher{nullptr};

std::string Narrow(const std::wstring& s) {
    if (s.empty()) return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()), nullptr, 0, nullptr, nullptr);
    std::string out(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()), out.data(), n, nullptr, nullptr);
    return out;
}

std::wstring LocalFolder() {
    if (g_local.empty()) g_local = std::wstring(winrt::Windows::Storage::ApplicationData::Current().LocalFolder().Path());
    return g_local;
}

void Log(const std::string& text) {
    std::lock_guard<std::mutex> lock(g_logLock);
    if (FILE* f = _wfopen((LocalFolder() + L"\\gamepart.txt").c_str(), L"ab")) {
        SYSTEMTIME t{};
        GetLocalTime(&t);
        std::fprintf(f, "%02u:%02u:%02u.%03u %s\n", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds, text.c_str());
        std::fclose(f);
    }
}

bool SafeGameName(const std::wstring& name) {
    if (name.empty() || name.size() > 200 || name == L"." || name == L"..") return false;
    for (wchar_t c : name) {
        if (c < 32 || c == L'\\' || c == L'/' || c == L':' || c == L'*' || c == L'?' || c == L'"' || c == L'<' || c == L'>' || c == L'|') return false;
    }
    return true;
}

const wchar_t* const kDriveGames = L"D:\\AnyPS5\\games";

bool IsFolder(const std::wstring& dir) {
    const DWORD attributes = GetFileAttributesW(dir.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY);
}

void ChooseGame(const std::wstring& name) {
    if (!SafeGameName(name)) {
        Log("rejected game name " + Narrow(name));
        return;
    }
    std::wstring dir = LocalFolder() + L"\\games\\" + name;
    if (!IsFolder(dir)) dir = std::wstring(kDriveGames) + L"\\" + name;
    if (!IsFolder(dir)) {
        Log("game folder missing: " + Narrow(name));
        return;
    }
    g_game = dir;
    g_running = name;
    const std::string path = Narrow(dir);
    if (FILE* f = _wfopen((LocalFolder() + L"\\game.txt").c_str(), L"wb")) {
        std::fwrite(path.data(), 1, path.size(), f);
        std::fclose(f);
    }
    if (FILE* f = _wfopen((LocalFolder() + L"\\last.txt").c_str(), L"wb")) {
        const std::string n = Narrow(name);
        std::fwrite(n.data(), 1, n.size(), f);
        std::fclose(f);
    }
    Log("game: games\\" + Narrow(name));
}

std::wstring LastGame() {
    std::wstring name;
    if (FILE* f = _wfopen((LocalFolder() + L"\\last.txt").c_str(), L"rb")) {
        char buf[512]{};
        const size_t n = std::fread(buf, 1, sizeof buf - 1, f);
        std::fclose(f);
        std::string s(buf, n);
        while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
        wchar_t wide[512]{};
        MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, wide, 511);
        name = wide;
    }
    return name;
}

void LaunchUri(const std::wstring& uri) {
    try {
        const bool ok = winrt::Windows::System::Launcher::LaunchUriAsync(winrt::Windows::Foundation::Uri(uri)).get();
        Log(std::string("launch ") + Narrow(uri.substr(0, uri.find(L'?'))) + (ok ? " ok" : " refused"));
    } catch (winrt::hresult_error const& e) {
        Log("launch " + Narrow(uri.substr(0, uri.find(L'?'))) + " failed " + Narrow(std::wstring(e.message())));
    }
}

void LaunchOnUi(const std::wstring& uri) {
    if (!g_dispatcher) {
        LaunchUri(uri);
        return;
    }
    HANDLE done = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    try {
        g_dispatcher.RunAsync(CoreDispatcherPriority::Normal, [uri, done] {
            try {
                winrt::Windows::System::Launcher::LaunchUriAsync(winrt::Windows::Foundation::Uri(uri)).Completed([uri, done](auto const& op, auto) {
                    try {
                        Log(std::string("launch ") + Narrow(uri.substr(0, uri.find(L'?'))) + (op.GetResults() ? " ok" : " refused"));
                    } catch (winrt::hresult_error const& e) {
                        Log("launch failed " + Narrow(std::wstring(e.message())));
                    }
                    SetEvent(done);
                });
            } catch (winrt::hresult_error const& e) {
                Log("launch failed " + Narrow(std::wstring(e.message())));
                SetEvent(done);
            }
        });
        WaitForSingleObject(done, 5000);
    } catch (winrt::hresult_error const& e) {
        Log("dispatch failed " + Narrow(std::wstring(e.message())));
    }
    CloseHandle(done);
}

std::wstring Escape(const std::wstring& text) {
    return std::wstring(winrt::Windows::Foundation::Uri::EscapeComponent(text));
}

void Leave(const std::wstring& uri, const char* why) {
    if (g_leaving.exchange(true)) return;
    Log(std::string("leaving: ") + why);
    LaunchOnUi(uri);
    Sleep(300);
    Log("game part exits (" + std::string(why) + ")");
    TerminateProcess(GetCurrentProcess(), 0);
}

void QuitToLibrary(const char* why) {
    Leave(L"anyps5:library?closed=" + Escape(g_running), why);
}

std::atomic<bool> g_menuOpen{false};
std::atomic<bool> g_pullMode{false};

void WriteLocal(const wchar_t* name, const std::string& text) {
    const std::wstring path = LocalFolder() + L"\\" + name, temp = path + L".tmp";
    if (FILE* f = _wfopen(temp.c_str(), L"wb")) {
        std::fwrite(text.data(), 1, text.size(), f);
        std::fclose(f);
        MoveFileExW(temp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING);
    }
}

std::string JsonText(const std::wstring& s) {
    return Narrow(std::wstring(winrt::Windows::Data::Json::JsonValue::CreateStringValue(s).Stringify()));
}

bool SafeRelative(const std::wstring& rel) {
    if (rel.empty() || rel.front() == L'/') return false;
    size_t start = 0;
    for (;;) {
        const size_t end = rel.find(L'/', start);
        const std::wstring part = rel.substr(start, end == std::wstring::npos ? std::wstring::npos : end - start);
        if (part.empty() || part == L"." || part == L".." || !SafeGameName(part)) return false;
        if (end == std::wstring::npos) return true;
        start = end + 1;
    }
}

std::wstring UrlPath(const std::wstring& rel) {
    std::wstring out;
    size_t start = 0;
    for (;;) {
        const size_t end = rel.find(L'/', start);
        out += Escape(rel.substr(start, end == std::wstring::npos ? std::wstring::npos : end - start));
        if (end == std::wstring::npos) return out;
        out += L'/';
        start = end + 1;
    }
}

uint64_t FetchTo(winrt::Windows::Web::Http::HttpClient const& http, const std::wstring& url, const std::wstring& dest) {
    using namespace winrt::Windows::Web::Http;
    using namespace winrt::Windows::Storage::Streams;
    auto response = http.GetAsync(winrt::Windows::Foundation::Uri(url), HttpCompletionOption::ResponseHeadersRead).get();
    response.EnsureSuccessStatusCode();
    auto input = response.Content().ReadAsInputStreamAsync().get();
    const std::wstring temp = dest + L".pull";
    HANDLE file = CreateFile2(temp.c_str(), GENERIC_WRITE, 0, CREATE_ALWAYS, nullptr);
    if (file == INVALID_HANDLE_VALUE) throw winrt::hresult_error(HRESULT_FROM_WIN32(GetLastError()), L"cannot create " + winrt::hstring(temp));
    uint64_t written = 0;
    Buffer buffer(4u << 20);
    try {
        for (;;) {
            auto chunk = input.ReadAsync(buffer, buffer.Capacity(), InputStreamOptions::None).get();
            if (chunk.Length() == 0) break;
            DWORD wrote = 0;
            if (!WriteFile(file, chunk.data(), chunk.Length(), &wrote, nullptr) || wrote != chunk.Length()) throw winrt::hresult_error(HRESULT_FROM_WIN32(GetLastError()), L"write failed");
            written += wrote;
        }
    } catch (...) {
        CloseHandle(file);
        DeleteFileW(temp.c_str());
        throw;
    }
    CloseHandle(file);
    if (!MoveFileExW(temp.c_str(), dest.c_str(), MOVEFILE_REPLACE_EXISTING)) throw winrt::hresult_error(HRESULT_FROM_WIN32(GetLastError()), L"rename failed");
    return written;
}

void PullGame() {
    using namespace winrt::Windows::Data::Json;
    const ULONGLONG started = GetTickCount64();
    std::wstring game;
    uint64_t bytes = 0, total = 0;
    uint32_t fetched = 0, skipped = 0, index = 0, count = 0;
    const auto seconds = [&] { return std::to_string((GetTickCount64() - started) / 1000); };
    const auto status = [&](const char* state, const std::string& extra) {
        WriteLocal(L"pull-status.txt", std::string("{\"state\":\"") + state + "\",\"game\":" + JsonText(game) + ",\"files\":" + std::to_string(count) + ",\"index\":" + std::to_string(index) + ",\"fetched\":" + std::to_string(fetched) + ",\"skipped\":" + std::to_string(skipped) + ",\"mb\":" + std::to_string(bytes >> 20) + ",\"totalMb\":" + std::to_string(total >> 20) + ",\"seconds\":" + seconds() + extra + "}");
    };
    try {
        std::string text;
        if (FILE* f = _wfopen((LocalFolder() + L"\\pull.json").c_str(), L"rb")) {
            char buf[4096];
            size_t n;
            while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) text.append(buf, n);
            std::fclose(f);
        }
        const auto job = JsonObject::Parse(winrt::to_hstring(text));
        const std::wstring base(job.GetNamedString(L"base"));
        game = std::wstring(job.GetNamedString(L"game"));
        if (!SafeGameName(game) || base.compare(0, 7, L"http://") != 0) throw winrt::hresult_error(E_INVALIDARG, L"bad pull job");
        Log("pull " + Narrow(game) + " to D:");
        const std::wstring stale = LocalFolder() + L"\\games\\" + game;
        if (IsFolder(stale)) {
            std::error_code ignored;
            std::filesystem::remove_all(stale, ignored);
            Log("removed the LocalState copy");
        }
        const std::wstring root = std::wstring(kDriveGames) + L"\\" + game;
        std::filesystem::create_directories(root);
        winrt::Windows::Web::Http::HttpClient http;
        const auto files = JsonArray::Parse(http.GetStringAsync(winrt::Windows::Foundation::Uri(base + L"/manifest/" + Escape(game))).get());
        count = files.Size();
        for (auto const& entry : files) total += static_cast<uint64_t>(entry.GetObject().GetNamedNumber(L"s"));
        ULONGLONG lastStatus = 0;
        for (auto const& entry : files) {
            ++index;
            const auto item = entry.GetObject();
            const std::wstring rel(item.GetNamedString(L"p"));
            const auto size = static_cast<uint64_t>(item.GetNamedNumber(L"s"));
            const auto modified = static_cast<int64_t>(item.GetNamedNumber(L"m", 0));
            if (!SafeRelative(rel)) throw winrt::hresult_error(E_INVALIDARG, L"bad path " + winrt::hstring(rel));
            std::wstring local = rel;
            for (auto& c : local) if (c == L'/') c = L'\\';
            const std::wstring dest = root + L"\\" + local;
            WIN32_FILE_ATTRIBUTE_DATA have{};
            if (GetFileAttributesExW(dest.c_str(), GetFileExInfoStandard, &have) && ((uint64_t(have.nFileSizeHigh) << 32) | have.nFileSizeLow) == size && ((int64_t((uint64_t(have.ftLastWriteTime.dwHighDateTime) << 32) | have.ftLastWriteTime.dwLowDateTime) - 116444736000000000LL) / 10000000) >= modified) {
                ++skipped;
            } else {
                std::filesystem::create_directories(std::filesystem::path(dest).parent_path());
                for (int attempt = 1;; ++attempt) {
                    try {
                        if (FetchTo(http, base + L"/file/" + Escape(game) + L"/" + UrlPath(rel), dest) != size) throw winrt::hresult_error(E_FAIL, L"size mismatch " + winrt::hstring(rel));
                        break;
                    } catch (winrt::hresult_error const& e) {
                        Log("pull retry " + std::to_string(attempt) + " " + Narrow(rel) + ": " + Narrow(std::wstring(e.message())));
                        if (attempt == 3) throw;
                        Sleep(1000);
                    }
                }
                ++fetched;
                bytes += size;
            }
            if (GetTickCount64() - lastStatus > 3000 || index == count) {
                status("pulling", "");
                lastStatus = GetTickCount64();
            }
        }
        status("done", "");
        Log("pull done: " + std::to_string(fetched) + " files, " + std::to_string(bytes >> 20) + " MB in " + seconds() + " s");
    } catch (winrt::hresult_error const& e) {
        Log("pull failed: " + Narrow(std::wstring(e.message())));
        status("failed", ",\"error\":" + JsonText(std::wstring(e.message())));
    } catch (std::exception const& e) {
        Log(std::string("pull failed: ") + e.what());
        status("failed", ",\"error\":" + JsonText(std::wstring(winrt::to_hstring(e.what()))));
    }
    DeleteFileW((LocalFolder() + L"\\pull.json").c_str());
}

std::wstring DisplayName(const std::wstring& folder) {
    const auto cut = folder.rfind(L" (");
    return cut == std::wstring::npos || cut == 0 ? folder : folder.substr(0, cut);
}

void OpenGameMenu() {
    if (!g_dispatcher || g_menuOpen.exchange(true)) return;
    Log("game menu");
    try {
        g_dispatcher.RunAsync(CoreDispatcherPriority::High, [] {
            try {
                using namespace winrt::Windows::UI::Popups;
                MessageDialog dialog(L"Hold View + Menu to open this menu again.", g_running.empty() ? L"AnyPS5" : winrt::hstring(DisplayName(g_running)));
                dialog.Commands().Append(UICommand(L"Resume", nullptr, winrt::box_value(0)));
                dialog.Commands().Append(UICommand(L"Close game", nullptr, winrt::box_value(1)));
                dialog.DefaultCommandIndex(0);
                dialog.CancelCommandIndex(0);
                dialog.ShowAsync().Completed([](auto const& op, auto) {
                    int choice = 0;
                    try {
                        choice = winrt::unbox_value<int>(op.GetResults().Id());
                    } catch (winrt::hresult_error const&) {
                    }
                    g_menuOpen = false;
                    Log(choice == 1 ? "menu: close game" : "menu: resume");
                    if (choice == 1) std::thread([] { QuitToLibrary("closed from the game menu"); }).detach();
                });
            } catch (winrt::hresult_error const& e) {
                g_menuOpen = false;
                Log("menu failed " + Narrow(std::wstring(e.message())));
            }
        });
    } catch (winrt::hresult_error const& e) {
        g_menuOpen = false;
        Log("menu dispatch failed " + Narrow(std::wstring(e.message())));
    }
}

void HandleActivation(winrt::Windows::ApplicationModel::Activation::IActivatedEventArgs const& args) {
    using namespace winrt::Windows::ApplicationModel::Activation;
    Log("activated kind " + std::to_string(static_cast<int>(args.Kind())));
    if (g_started) {
        if (args.Kind() != ActivationKind::Protocol) {
            Log("resumed");
            return;
        }
        std::wstring quit, wanted;
        try {
            const auto query = args.as<ProtocolActivatedEventArgs>().Uri().QueryParsed();
            for (auto const& entry : query) {
                if (entry.Name() == L"quit") quit = entry.Value();
                if (entry.Name() == L"game") wanted = entry.Value();
            }
        } catch (winrt::hresult_error const& e) {
            Log("bad launch link " + Narrow(std::wstring(e.message())));
            return;
        }
        if (!quit.empty()) {
            std::thread([] { QuitToLibrary("closed from the library"); }).detach();
            return;
        }
        if (wanted.empty() || wanted == g_running) {
            Log("resumed");
            return;
        }
        if (!SafeGameName(wanted)) {
            Log("rejected game name " + Narrow(wanted));
            return;
        }
        std::thread([wanted] { Leave(L"anyps5:library?play=" + Escape(wanted), "another game was chosen"); }).detach();
        return;
    }
    if (args.Kind() != ActivationKind::Protocol) {
        const std::wstring last = LastGame();
        if (!last.empty()) ChooseGame(last);
        return;
    }
    const auto uri = args.as<ProtocolActivatedEventArgs>().Uri();
    try {
        const auto query = uri.QueryParsed();
        for (auto const& entry : query) {
            if (entry.Name() == L"pull" && entry.Value() == L"1") {
                g_pullMode = true;
                return;
            }
        }
        ChooseGame(std::wstring(query.GetFirstValueByName(L"game")));
    } catch (winrt::hresult_error const& e) {
        Log("bad launch link " + Narrow(std::wstring(e.message())));
    }
}

using PayloadMain = int(__cdecl*)(const wchar_t*, char*, size_t);

HMODULE ResolveModule(const char* name) {
    wchar_t wide[260]{};
    MultiByteToWideChar(CP_UTF8, 0, name, -1, wide, 259);
    if (HMODULE module = GetModuleHandleW(wide)) return module;
    if (HMODULE module = LoadPackagedLibrary(wide, 0)) return module;
    using LoadLibraryExWFn = HMODULE(WINAPI*)(LPCWSTR, HANDLE, DWORD);
    static auto loadLibrary = reinterpret_cast<LoadLibraryExWFn>(GetProcAddress(GetModuleHandleW(L"kernelbase.dll"), "LoadLibraryExW"));
    return loadLibrary ? loadLibrary(wide, nullptr, 0) : nullptr;
}

std::string RunPayload(const std::wstring& path, const std::wstring& folder) {
    FILE* file = _wfopen(path.c_str(), L"rb");
    if (!file) return "no payload";
    std::string image;
    char chunk[65536];
    for (size_t got; (got = std::fread(chunk, 1, sizeof chunk, file)) != 0;) image.append(chunk, got);
    std::fclose(file);
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(image.data());
    if (image.size() < sizeof(IMAGE_DOS_HEADER) || dos->e_magic != IMAGE_DOS_SIGNATURE) return "bad dos header";
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(image.data() + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) return "bad nt header";
    const SIZE_T size = nt->OptionalHeader.SizeOfImage;
    auto* base = static_cast<unsigned char*>(VirtualAllocFromApp(nullptr, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    if (!base) return "alloc failed " + std::to_string(GetLastError());
    std::memcpy(base, image.data(), nt->OptionalHeader.SizeOfHeaders);
    const auto* section = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++section) {
        if (section->SizeOfRawData) std::memcpy(base + section->VirtualAddress, image.data() + section->PointerToRawData, section->SizeOfRawData);
    }
    const auto& directories = nt->OptionalHeader.DataDirectory;
    const auto delta = reinterpret_cast<std::uint64_t>(base) - nt->OptionalHeader.ImageBase;
    const auto& relocs = directories[IMAGE_DIRECTORY_ENTRY_BASERELOC];
    if (delta && relocs.Size) {
        auto* block = reinterpret_cast<IMAGE_BASE_RELOCATION*>(base + relocs.VirtualAddress);
        auto* end = reinterpret_cast<unsigned char*>(block) + relocs.Size;
        while (reinterpret_cast<unsigned char*>(block) < end && block->SizeOfBlock) {
            const auto* entries = reinterpret_cast<const WORD*>(block + 1);
            const size_t count = (block->SizeOfBlock - sizeof(IMAGE_BASE_RELOCATION)) / sizeof(WORD);
            for (size_t i = 0; i < count; ++i) {
                if ((entries[i] >> 12) == IMAGE_REL_BASED_DIR64) *reinterpret_cast<std::uint64_t*>(base + block->VirtualAddress + (entries[i] & 0xFFF)) += delta;
            }
            block = reinterpret_cast<IMAGE_BASE_RELOCATION*>(reinterpret_cast<unsigned char*>(block) + block->SizeOfBlock);
        }
    }
    std::string missing;
    const auto& imports = directories[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (imports.Size) {
        for (auto* descriptor = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + imports.VirtualAddress); descriptor->Name; ++descriptor) {
            const char* dll = reinterpret_cast<const char*>(base + descriptor->Name);
            HMODULE module = ResolveModule(dll);
            auto* thunk = reinterpret_cast<IMAGE_THUNK_DATA64*>(base + descriptor->FirstThunk);
            const auto* lookup = reinterpret_cast<const IMAGE_THUNK_DATA64*>(base + (descriptor->OriginalFirstThunk ? descriptor->OriginalFirstThunk : descriptor->FirstThunk));
            for (; lookup->u1.AddressOfData; ++lookup, ++thunk) {
                FARPROC proc = nullptr;
                std::string what;
                if (IMAGE_SNAP_BY_ORDINAL64(lookup->u1.Ordinal)) {
                    what = "#" + std::to_string(IMAGE_ORDINAL64(lookup->u1.Ordinal));
                    if (module) proc = GetProcAddress(module, reinterpret_cast<LPCSTR>(IMAGE_ORDINAL64(lookup->u1.Ordinal)));
                } else {
                    what = reinterpret_cast<const IMAGE_IMPORT_BY_NAME*>(base + lookup->u1.AddressOfData)->Name;
                    if (module) proc = GetProcAddress(module, what.c_str());
                }
                if (!proc) missing += (missing.empty() ? "" : ",") + std::string(dll) + "!" + what;
                thunk->u1.Function = reinterpret_cast<std::uint64_t>(proc);
            }
        }
    }
    if (!missing.empty()) return "missing imports " + missing;
    section = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++section) {
        const DWORD c = section->Characteristics;
        ULONG protect = PAGE_READONLY;
        if (c & IMAGE_SCN_MEM_EXECUTE) protect = (c & IMAGE_SCN_MEM_WRITE) ? PAGE_EXECUTE_READWRITE : PAGE_EXECUTE_READ;
        else if (c & IMAGE_SCN_MEM_WRITE) protect = PAGE_READWRITE;
        ULONG old = 0;
        const SIZE_T length = section->Misc.VirtualSize ? section->Misc.VirtualSize : section->SizeOfRawData;
        if (length && !VirtualProtectFromApp(base + section->VirtualAddress, length, protect, &old)) return "protect failed " + std::to_string(GetLastError());
    }
    const auto& exceptions = directories[IMAGE_DIRECTORY_ENTRY_EXCEPTION];
    if (exceptions.Size) {
        RtlAddFunctionTable(reinterpret_cast<PRUNTIME_FUNCTION>(base + exceptions.VirtualAddress), exceptions.Size / sizeof(RUNTIME_FUNCTION), reinterpret_cast<DWORD64>(base));
    }
    using DllMainFn = BOOL(WINAPI*)(HINSTANCE, DWORD, LPVOID);
    if (nt->OptionalHeader.AddressOfEntryPoint) {
        auto entry = reinterpret_cast<DllMainFn>(base + nt->OptionalHeader.AddressOfEntryPoint);
        if (!entry(reinterpret_cast<HINSTANCE>(base), DLL_PROCESS_ATTACH, nullptr)) return "dllmain failed";
    }
    const auto& exports = directories[IMAGE_DIRECTORY_ENTRY_EXPORT];
    if (!exports.Size) return "no exports";
    const auto* table = reinterpret_cast<const IMAGE_EXPORT_DIRECTORY*>(base + exports.VirtualAddress);
    const auto* names = reinterpret_cast<const DWORD*>(base + table->AddressOfNames);
    const auto* ordinals = reinterpret_cast<const WORD*>(base + table->AddressOfNameOrdinals);
    const auto* functions = reinterpret_cast<const DWORD*>(base + table->AddressOfFunctions);
    for (DWORD i = 0; i < table->NumberOfNames; ++i) {
        if (std::strcmp(reinterpret_cast<const char*>(base + names[i]), "PayloadMain") == 0) {
            auto main = reinterpret_cast<PayloadMain>(base + functions[ordinals[i]]);
            std::string output(1 << 16, static_cast<char>(0));
            const int code = main(folder.c_str(), output.data(), output.size());
            return "game host returned " + std::to_string(code);
        }
    }
    return "no PayloadMain";
}

std::wstring PayloadPath() {
    const std::wstring local = LocalFolder() + L"\\payload.dll";
    if (GetFileAttributesW(local.c_str()) != INVALID_FILE_ATTRIBUTES) return local;
    return std::wstring(winrt::Windows::ApplicationModel::Package::Current().InstalledLocation().Path()) + L"\\payload.dll";
}

void RunGame() {
    try {
        const auto limit = winrt::Windows::System::MemoryManager::AppMemoryUsageLimit();
        Log("memory limit " + std::to_string(limit >> 20) + " MB, in use " + std::to_string(winrt::Windows::System::MemoryManager::AppMemoryUsage() >> 20) + " MB");
    } catch (...) {
        Log("memory limit unknown");
    }
    if (g_game.empty()) {
        Log("no game chosen; using game.txt");
    }
    const std::wstring payload = PayloadPath();
    Log(std::string("payload from ") + (payload.compare(0, LocalFolder().size(), LocalFolder()) == 0 ? "LocalState" : "the package"));
    std::string result;
    try {
        result = RunPayload(payload, LocalFolder());
    } catch (...) {
        result = "payload threw";
    }
    Log(result);
}

std::string TakeCommand() {
    const std::wstring path = LocalFolder() + L"\\gamecmd.txt";
    FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) return {};
    char buf[64]{};
    const size_t n = std::fread(buf, 1, sizeof buf - 1, f);
    std::fclose(f);
    DeleteFileW(path.c_str());
    std::string s(buf, n);
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
    return s;
}

void WatchQuitCombo() {
    using namespace winrt::Windows::Gaming::Input;
    ULONGLONG heldSince = 0;
    bool fired = false;
    size_t padsSeen = static_cast<size_t>(-1);
    for (unsigned tick = 0;; ++tick) {
        bool both = false;
        try {
            const auto pads = Gamepad::Gamepads();
            if (pads.Size() != padsSeen) {
                padsSeen = pads.Size();
                Log("pads " + std::to_string(padsSeen));
            }
            for (auto const& pad : pads) {
                const auto buttons = pad.GetCurrentReading().Buttons;
                if ((buttons & GamepadButtons::View) == GamepadButtons::View && (buttons & GamepadButtons::Menu) == GamepadButtons::Menu) both = true;
            }
        } catch (...) {
        }
        const ULONGLONG now = GetTickCount64();
        if (!both) {
            heldSince = 0;
            fired = false;
        } else if (!heldSince) {
            heldSince = now;
        } else if (!fired && now - heldSince >= 1000) {
            fired = true;
            OpenGameMenu();
        }
        if (tick % 10 == 0) {
            const std::string command = TakeCommand();
            if (command == "menu") OpenGameMenu();
            else if (command == "quit") QuitToLibrary("quit command");
            else if (!command.empty()) Log("unknown command " + command);
        }
        Sleep(50);
    }
}

struct App : implements<App, IFrameworkViewSource, IFrameworkView> {
    IFrameworkView CreateView() { return *this; }
    void Initialize(CoreApplicationView const& view) {
        view.Activated([](CoreApplicationView const&, winrt::Windows::ApplicationModel::Activation::IActivatedEventArgs const& args) {
            HandleActivation(args);
            g_activated = true;
            CoreWindow::GetForCurrentThread().Activate();
        });
    }
    void Load(hstring const&) {}
    void Uninitialize() {}
    void SetWindow(CoreWindow const&) {}
    void Run() {
        auto window = CoreWindow::GetForCurrentThread();
        g_dispatcher = window.Dispatcher();
        window.Activate();
        window.Dispatcher().ProcessEvents(CoreProcessEventsOption::ProcessAllIfPresent);
        std::atomic<bool> done{false};
        const ULONGLONG start = GetTickCount64();
        while (!g_activated && GetTickCount64() - start < 5000) {
            window.Dispatcher().ProcessEvents(CoreProcessEventsOption::ProcessAllIfPresent);
            Sleep(5);
        }
        if (g_running.empty()) g_running = LastGame();
        g_started = true;
        DeleteFileW((LocalFolder() + L"\\gamecmd.txt").c_str());
        std::thread([] { WatchQuitCombo(); }).detach();
        std::thread worker([&] {
            if (g_pullMode) PullGame(); else RunGame();
            done = true;
        });
        while (!done) {
            window.Dispatcher().ProcessEvents(CoreProcessEventsOption::ProcessAllIfPresent);
            Sleep(8);
        }
        worker.join();
        if (g_pullMode) std::thread([] { Leave(L"anyps5:library", "download finished"); }).detach();
        else std::thread([] { QuitToLibrary("the game ended"); }).detach();
        for (;;) {
            window.Dispatcher().ProcessEvents(CoreProcessEventsOption::ProcessAllIfPresent);
            Sleep(8);
        }
    }
};

}

int __stdcall wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    init_apartment();
    const auto v = winrt::Windows::ApplicationModel::Package::Current().Id().Version();
    Log("game part started, package " + std::to_string(v.Major) + "." + std::to_string(v.Minor) + "." + std::to_string(v.Build) + "." + std::to_string(v.Revision));
    CoreApplication::Run(make<App>());
}
