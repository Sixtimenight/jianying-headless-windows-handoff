// Process-isolated headless export for the reviewed Windows Jianying 11.5.0.14471 engine.
// Windows counterpart of engine/native_export.cpp. Build with MSVC (/MD, x64): the engine
// is an MSVC /MD binary and its interfaces pass std::string, std::shared_ptr and
// std::function by value. No UI attachment, account session, network, or app changes.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

#pragma comment(lib, "bcrypt.lib")

namespace lvve {
struct Draft;
struct PersistentDraft;
struct VEGlobalConfig;
namespace adapter { struct VEAdapterConfig; }
}

// Request types mirror the engine's MSVC layouts (verified from the engine's own
// factories and RTTI; see docs/windows-native.md). Names match the engine's so
// RTTI name comparison behaves as for engine-created requests.
namespace lyra {
struct RespStruct;
struct ReqStruct {
  virtual ~ReqStruct() = default;
  std::string service, api;
  int tid = -1;
  bool async = false;
  int flags = 0;
};
struct InitReqStruct : ReqStruct {
  std::string project_id;
  int main_config = -1;
  bool render_track = true, mixed_track = false, float_render = false, reserved = false;
  std::string timeline_name;
  std::shared_ptr<lvve::PersistentDraft> draft;
  InitReqStruct() { service = "ProjectService"; api = "init"; }
};
struct DraftInitReqStruct : ReqStruct {
  int input_kind = 2;
  std::string json;
  std::shared_ptr<lvve::Draft> draft;
  bool option_a = false, option_b = true;
  DraftInitReqStruct() { service = "DraftService"; api = "draftInit"; }
};
static_assert(sizeof(ReqStruct) == 0x58, "ReqStruct layout");
static_assert(sizeof(InitReqStruct) == 0xb0, "InitReqStruct layout");
static_assert(sizeof(DraftInitReqStruct) == 0x98, "DraftInitReqStruct layout");
static_assert(sizeof(std::string) == 32, "MSVC std::string");
struct __single_inheritance Server {};
struct __single_inheritance Session {};
}
namespace lvve { struct __single_inheritance Logger {}; }

using Resp = std::shared_ptr<lyra::RespStruct>;
using Req = std::shared_ptr<lyra::ReqStruct>;
using DraftPtr = std::shared_ptr<lvve::Draft>;
using AlogFn = void (*)(const char*, const char*, int, const char*, int, const char*, ...);

// Offsets inside the reviewed videoeditor.dll (RVA). Exported functions are
// resolved by name; these two are internal and pinned to the DLL hash.
struct NativeAbi {
  const char* version;
  const char* sha256;
  uintptr_t make_export_request;  // std::make_shared<ExportStartReqStruct>()
  uintptr_t restore_draft;        // void (int sid, bool, int tid)
  const char* restore_event;
};
static const NativeAbi abi_profiles[] = {
  {"11.5.0.14471", "37cadef37daff82e2ecdcd65080b9cbd7f6f9436f5e56c1ffb90c52c408eb7fb",
   0x11b3ac0, 0x11c41a0, "DraftService::restoreDraft driverRun, callback !"},
};
// ExportStartReqStruct (object) and its packed adapter::ExportConfig.
constexpr size_t kReqPath = 0x58, kReqConfig = 0x78;
constexpr size_t kCfgWidth = 0x47, kCfgHeight = 0x4b, kCfgHardware = 0x4f, kCfgFps = 0x52, kCfgBitrate = 0x66;
// RespStruct: tid and status code.
constexpr size_t kRespTid = 0x48, kRespCode = 0x4c;

static std::atomic<bool> compile_done{false}, compile_error{false}, restore_done{false};
static std::atomic<int> callback_error{0};
static std::mutex logging_mutex;
static const NativeAbi* active_abi = nullptr;

static void nativeLog(const char* tag, const char* file, int line, const char* function,
                      int level, const char* format, ...) {
  char rendered[16384];
  va_list values;
  va_start(values, format);
  std::vsnprintf(rendered, sizeof(rendered), format ? format : "", values);
  va_end(values);
  // Progress < 1 is possible on the final callback, so progress alone is not completion.
  if (std::strstr(rendered, "export_callback: VE_INFO_COMPILE_DONE")) compile_done = true;
  if (std::strstr(rendered, "export_callback: VE_ERROR_COMPILE")) compile_error = true;
  // Nested clips restore asynchronously; export only after the full restore driver run.
  if (active_abi && std::strstr(rendered, active_abi->restore_event)) restore_done = true;
  std::lock_guard<std::mutex> lock(logging_mutex);
  std::fprintf(stderr, "ENGINE [%d] %s:%d %s: %s\n", level, file ? file : "", line,
               function ? function : "", rendered);
}

static std::string sha256_file(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot read engine library");
  BCRYPT_ALG_HANDLE alg = nullptr;
  BCRYPT_HASH_HANDLE hash = nullptr;
  if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) ||
      BCryptCreateHash(alg, &hash, nullptr, 0, nullptr, 0, 0))
    throw std::runtime_error("SHA-256 unavailable");
  std::string chunk(1 << 20, '\0');
  while (input) {
    input.read(chunk.data(), chunk.size());
    if (input.gcount())
      BCryptHashData(hash, reinterpret_cast<PUCHAR>(chunk.data()), static_cast<ULONG>(input.gcount()), 0);
  }
  unsigned char digest[32];
  BCryptFinishHash(hash, digest, sizeof(digest), 0);
  BCryptDestroyHash(hash);
  BCryptCloseAlgorithmProvider(alg, 0);
  std::string hex;
  for (auto byte : digest) {
    hex += "0123456789abcdef"[byte >> 4];
    hex += "0123456789abcdef"[byte & 15];
  }
  return hex;
}

static std::string utf8(const std::wstring& text) {
  if (text.empty()) return {};
  int n = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
  std::string out(static_cast<size_t>(n), '\0');
  WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), n, nullptr, nullptr);
  return out;
}

class Engine {
 public:
  explicit Engine(const std::filesystem::path& install) {
    SetDllDirectoryW(install.c_str());
    module_ = LoadLibraryExW((install / L"videoeditor.dll").c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module_) throw std::runtime_error("cannot load videoeditor.dll (error " + std::to_string(GetLastError()) + ")");
    base_ = reinterpret_cast<char*>(module_);
  }
  template <typename F> F fn(const char* name) const {
    auto p = GetProcAddress(module_, name);
    if (!p) throw std::runtime_error(std::string("missing engine export: ") + name);
    return reinterpret_cast<F>(p);
  }
  // Member functions: x64 MSVC member pointers for single-inheritance classes are one code pointer.
  template <typename M> M member(const char* name) const {
    void* p = reinterpret_cast<void*>(GetProcAddress(module_, name));
    if (!p) throw std::runtime_error(std::string("missing engine export: ") + name);
    static_assert(sizeof(M) == sizeof(void*), "unexpected member pointer size");
    M m;
    std::memcpy(&m, &p, sizeof(p));
    return m;
  }
  template <typename F> F internal(uintptr_t rva) const { return reinterpret_cast<F>(base_ + rva); }

 private:
  HMODULE module_ = nullptr;
  char* base_ = nullptr;
};

template <typename T> static T field(const void* pointer, size_t offset) {
  T result{};
  std::memcpy(&result, reinterpret_cast<const char*>(pointer) + offset, sizeof(T));
  return result;
}
static void checkResponse(const Resp& response, const char* stage) {
  if (!response) throw std::runtime_error(std::string(stage) + " (no response)");
  int code = field<int>(response.get(), kRespCode);
  if (code != 0) throw std::runtime_error(std::string(stage) + " (code " + std::to_string(code) + ")");
}

int wmain(int argc, wchar_t** argv) {
  try {
    if (argc != 10)
      throw std::runtime_error("usage: helper install_dir timeline.json output.mp4 width height fps bitrate timeout_seconds dll_sha256");
    const std::filesystem::path install = std::filesystem::canonical(argv[1]);
    const auto input = std::filesystem::canonical(argv[2]);
    const auto output = std::filesystem::absolute(argv[3]);
    if (std::filesystem::exists(output) || output.extension() != L".mp4")
      throw std::runtime_error("output must be a new MP4 in the owned job");
    int width = std::stoi(argv[4]), height = std::stoi(argv[5]), timeout = std::stoi(argv[8]);
    double fps = std::stod(argv[6]);
    long long bitrate = std::stoll(argv[7]);
    if (width < 16 || width > 7680 || width % 2 || height < 16 || height > 7680 || height % 2 ||
        !std::isfinite(fps) || fps < 1 || fps > 120 || bitrate < 100000 || bitrate > 200000000 ||
        timeout < 5 || timeout > 43200)
      throw std::runtime_error("invalid export settings");

    const std::string hash = sha256_file(install / L"videoeditor.dll");
    if (hash != utf8(argv[9])) throw std::runtime_error("engine differs from the requested reviewed hash");
    for (const auto& profile : abi_profiles)
      if (hash == profile.sha256) active_abi = &profile;
    if (!active_abi) throw std::runtime_error("engine differs from the supported ABI");
    std::cerr << "JY_NATIVE_ABI " << active_abi->version << '\n';

    std::ifstream source(input, std::ios::binary);
    std::string json((std::istreambuf_iterator<char>(source)), std::istreambuf_iterator<char>());
    if (json.empty()) throw std::runtime_error("empty timeline input");

    // Hard deadline in place of POSIX alarm().
    std::thread([timeout] {
      std::this_thread::sleep_for(std::chrono::seconds(timeout + 15));
      std::fprintf(stderr, "JY_NATIVE_EXPORT_FAILED: watchdog timeout\n");
      TerminateProcess(GetCurrentProcess(), 3);
    }).detach();

    SetCurrentDirectoryW(install.c_str());
    Engine engine(install);

    using namespace lyra;
    auto getLogger = engine.fn<lvve::Logger& (*)()>("?getLogger@Logger@lvve@@SAAEAV12@XZ");
    auto setAlog = engine.member<void (lvve::Logger::*)(AlogFn, void (*)(char, char))>(
        "?setAlogFunction@Logger@lvve@@QEAAXP6AXPEBD0H0H0ZZP6AXDD@Z@Z");
    auto setLevel = engine.member<void (lvve::Logger::*)(const std::string&)>(
        "?setLogLevel@Logger@lvve@@QEAAXAEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z");
    auto instance = engine.fn<Server* (*)()>("?instance@Server@lyra@@SAPEAU12@XZ");
    auto startup = engine.member<void (Server::*)(const lvve::VEGlobalConfig*,
                                                  const std::function<void(const std::function<void()>&)>&)>(
        "?startup@Server@lyra@@QEAAXPEBUVEGlobalConfig@lvve@@AEBV?$function@$$A6AXAEBV?$function@$$A6AXXZ@std@@@Z@std@@@Z");
    auto openSession = engine.member<long (Server::*)(const lvve::adapter::VEAdapterConfig*, const std::string&)>(
        "?openSession@Server@lyra@@QEAAJPEBUVEAdapterConfig@adapter@lvve@@AEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z");
    auto getSession = engine.member<std::shared_ptr<Session> (Server::*)(long)>(
        "?getSession@Server@lyra@@QEAA?AV?$shared_ptr@USession@lyra@@@std@@J@Z");
    auto invoke = engine.member<Resp (Server::*)(Req, long)>(
        "?invoke@Server@lyra@@QEAA?AV?$shared_ptr@URespStruct@lyra@@@std@@V?$shared_ptr@UReqStruct@lyra@@@4@J@Z");
    auto invokeSync = engine.member<void (Server::*)(Req, const std::function<void(Resp)>&, long)>(
        "?invokeSync@Server@lyra@@QEAAXV?$shared_ptr@UReqStruct@lyra@@@std@@AEBV?$function@$$A6AXV?$shared_ptr@URespStruct@lyra@@@std@@@Z@4@J@Z");
    auto pumpOnce = engine.member<bool (Server::*)()>("?pumpOnce@Server@lyra@@QEAA_NXZ");
    auto closeSession = engine.member<void (Server::*)(long, std::function<void()>)>(
        "?closeSession@Server@lyra@@QEAAXJV?$function@$$A6AXXZ@std@@@Z");
    auto shutdown = engine.member<void (Server::*)()>("?shutdown@Server@lyra@@QEAAXXZ");
    auto getVeWrapper = engine.member<std::shared_ptr<void> (Session::*)()>(
        "?getVeWrapper@Session@lyra@@QEAA?AV?$shared_ptr@UVeWrapper@wrapper@lyra@@@std@@XZ");
    auto draftTransaction = engine.member<DraftPtr (Session::*)(const std::function<void(DraftPtr)>&, long)>(
        "?draftTransaction@Session@lyra@@QEAA?AV?$shared_ptr@VDraft@lvve@@@std@@AEBV?$function@$$A6AXV?$shared_ptr@VDraft@lvve@@@std@@@Z@4@J@Z");
    auto projectInit = engine.fn<Resp (*)(std::shared_ptr<InitReqStruct>, long)>(
        "?init@ProjectClient@@SA?AV?$shared_ptr@UInitRespStruct@lyra@@@std@@V?$shared_ptr@UInitReqStruct@lyra@@@3@J@Z");
    auto deserialize = engine.fn<std::shared_ptr<lvve::PersistentDraft> (*)(const std::string&)>(
        "?deserialize_persistent_draft@Deserializer@lvve@@SA?AV?$shared_ptr@VPersistentDraft@lvve@@@std@@AEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@4@@Z");
    auto draftFromJson = engine.fn<DraftPtr (*)(const std::string&)>(
        "?GetDraftFromJson@lvve@@YA?AV?$shared_ptr@VDraft@lvve@@@std@@AEBV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@3@@Z");
    auto jsonFromDraft = engine.fn<std::string (*)(const DraftPtr&)>(
        "?GetJsonFromDraft@lvve@@YA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@AEBV?$shared_ptr@VDraft@lvve@@@3@@Z");
    auto makeExportRequest = engine.internal<Req (*)()>(active_abi->make_export_request);
    auto restoreDraft = engine.internal<void (*)(int, bool, int)>(active_abi->restore_draft);

    lvve::Logger& logger = getLogger();
    (logger.*setAlog)(nativeLog, nullptr);
    (logger.*setLevel)("debug");
    Server* server = instance();
    if (!server) throw std::runtime_error("native server unavailable");
    auto pump = [&](int milliseconds) {
      auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(milliseconds);
      while (std::chrono::steady_clock::now() < until) {
        (server->*pumpOnce)();
        Sleep(10);
      }
    };
    (server->*startup)(nullptr, [](const std::function<void()>& f) { if (f) f(); });
    std::cerr << "JY_NATIVE_STARTED\n";
    long sid = (server->*openSession)(nullptr, "isolated-local-export");
    auto session = (server->*getSession)(sid);
    if (!session || !((*session).*getVeWrapper)()) throw std::runtime_error("native timeline engine unavailable");
    std::cerr << "JY_NATIVE_SESSION " << sid << '\n';

    auto project = std::make_shared<InitReqStruct>();
    project->project_id = "isolated-local-export";
    project->timeline_name = "isolated-local-export";
    project->draft = deserialize(json);
    if (!project->draft) throw std::runtime_error("persistent draft decode failed");
    auto initialized = projectInit(project, sid);
    checkResponse(initialized, "project initialization failed");
    int tid = field<int>(initialized.get(), kRespTid);
    std::cerr << "JY_NATIVE_TIMELINE " << tid << '\n';

    auto binding = std::make_shared<DraftInitReqStruct>();
    binding->tid = tid;
    binding->draft = draftFromJson(json);
    if (!binding->draft) throw std::runtime_error("runtime draft decode failed");
    checkResponse((server->*invoke)(binding, sid), "runtime draft initialization failed");

    restoreDraft(static_cast<int>(sid), true, tid);
    const auto restore_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout);
    while (!restore_done && std::chrono::steady_clock::now() < restore_deadline) pump(20);
    if (!restore_done) throw std::runtime_error("native timeline restoration did not finish");
    std::cerr << "JY_NATIVE_RESTORE_DONE\n";

    bool ready = false;
    ((*session).*draftTransaction)([&](DraftPtr draft) {
      ready = bool(draft);
      if (draft) {
        auto snapshot = output.parent_path() / L"runtime-timeline.json";
        if (std::filesystem::exists(snapshot)) throw std::runtime_error("runtime snapshot already exists");
        std::ofstream saved(snapshot, std::ios::binary);
        saved << jsonFromDraft(draft);
        if (!saved) throw std::runtime_error("runtime snapshot write failed");
      }
    }, tid);
    if (!ready) throw std::runtime_error("session has no draft after initialization");

    Req request = makeExportRequest();
    if (!request || request->service != "ExportService" || request->api != "exportStart")
      throw std::runtime_error("unexpected native export request identity");
    request->tid = tid;
    char* storage = reinterpret_cast<char*>(request.get());
    *reinterpret_cast<std::string*>(storage + kReqPath) = utf8(output.wstring());
    char* config = storage + kReqConfig;
    std::memcpy(config + kCfgWidth, &width, sizeof(width));
    std::memcpy(config + kCfgHeight, &height, sizeof(height));
    config[kCfgHardware] = 0;  // Native hardware-encode preference, not an encoder guarantee.
    std::memcpy(config + kCfgFps, &fps, sizeof(fps));
    std::memcpy(config + kCfgBitrate, &bitrate, sizeof(bitrate));
    (server->*invokeSync)(request, [](Resp response) {
      int code = response ? field<int>(response.get(), kRespCode) : -999;
      if (code) callback_error = code;
    }, sid);
    auto until = std::chrono::steady_clock::now() + std::chrono::seconds(timeout);
    while (!compile_done && !compile_error && !callback_error && std::chrono::steady_clock::now() < until)
      pump(20);
    bool success = compile_done && !compile_error && !callback_error;
    if (success) pump(500);  // Drain the native completion/encoder-close task.
    if (callback_error) std::cerr << "JY_NATIVE_EXPORT_CALLBACK_CODE " << callback_error.load() << '\n';
    request.reset(); binding.reset(); project.reset(); session.reset(); initialized.reset();
    (server->*closeSession)(sid, [] {});
    pump(100);
    (server->*shutdown)();
    if (!success) throw std::runtime_error("native export failed or timed out; retained partial output is not a deliverable");
    if (!std::filesystem::exists(output) || std::filesystem::file_size(output) == 0)
      throw std::runtime_error("native completion had no nonempty output");
    std::cout << "JY_NATIVE_EXPORT_DONE" << std::endl;
    std::fflush(stderr);
    TerminateProcess(GetCurrentProcess(), 0);  // Skip engine static destructors.
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "JY_NATIVE_EXPORT_FAILED: " << error.what() << std::endl;
    return 1;
  }
}
