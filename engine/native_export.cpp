// Process-isolated adapters for exact signed Jianying 11.5.0 and 11.4.2 engines.
// No UI attachment, account session, network, or modifications to the app.
#include <CommonCrypto/CommonDigest.h>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace lvve {
struct Draft; struct PersistentDraft; struct VEGlobalConfig;
namespace adapter { struct VEAdapterConfig; }
std::shared_ptr<Draft> GetDraftFromJson(const std::string&);
std::string GetJsonFromDraft(const std::shared_ptr<Draft>&);
class Deserializer { public:
  static std::shared_ptr<PersistentDraft> deserialize_persistent_draft(const std::string&);
};
class Logger { public:
  static Logger* getLogger();
  void setLogLevel(const std::string&);
  void setAlogFunction(void (*)(const char*, const char*, int, const char*, int, const char*, ...), void (*)(char, char));
};
}
namespace lyra {
struct RespStruct;
struct ReqStruct {
  virtual ~ReqStruct() = default;
  std::string service, api;
  long tid = -1;
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
static_assert(sizeof(ReqStruct) == 0x48 && sizeof(InitReqStruct) == 0x90);
static_assert(sizeof(DraftInitReqStruct) == 0x80 && sizeof(std::string) == 24);
class Session { public:
  std::shared_ptr<void> getVeWrapper();
  std::shared_ptr<RespStruct> draftTransaction(const std::function<void(std::shared_ptr<lvve::Draft>)>&, long);
};
class Server { public:
  static Server& instance();
  void startup(const lvve::VEGlobalConfig*, const std::function<void(const std::function<void()>&)>&);
  long openSession(const lvve::adapter::VEAdapterConfig*, const std::string&);
  std::shared_ptr<Session> getSession(long);
  std::shared_ptr<RespStruct> invoke(std::shared_ptr<ReqStruct>, long);
  void invokeSync(std::shared_ptr<ReqStruct>, const std::function<void(std::shared_ptr<RespStruct>)>&, long);
  void closeSession(long, std::function<void()>);
  void pumpOnce();
  void shutdown();
};
}
class ProjectClient { public:
  static std::shared_ptr<lyra::RespStruct> init(std::shared_ptr<lyra::InitReqStruct>, long);
};
namespace lyra::wrapper { class VeWrapper { public:
  std::shared_ptr<lvve::adapter::VEAdapterConfig> getVeAdapterConfig();
}; }

static std::atomic<bool> compile_done{false}, compile_error{false}, restore_done{false};
static std::atomic<int> callback_error{0};
static std::mutex logging_mutex;

struct NativeAbi {
  const char* version;
  const char* sha256;
  size_t restore_draft, export_constructor, export_request_size, mask_hub;
  int restore_line;
  const char* restore_event;
};
// 11.4.2 constants are retained. Each new row requires disassembly and native
// fixture validation; a matching marketing version alone is never sufficient.
static const NativeAbi abi_profiles[] = {
  {"11.5.0", "2041482a1aaeffa4d8bd69b836f8cf38807aaad8021bca410d567c59af3bccfa",
   0x21b86dc, 0x274b018, 0x3d8, 0x7e8, 669,
   "[draft_service.cpp:operator():669][LYRA] [LYRA] DraftService::restoreDraft driverRun, callback !"},
  {"11.4.2", "632c8ddd09ff4a54f876cd8142eb505055ee26d944199506b230949b7e106bd1",
   0x21234d0, 0x2681f98, 0x3d8, 0x7e8, 669,
   "[draft_service.cpp:operator():669][LYRA] [LYRA] DraftService::restoreDraft driverRun, callback !"},
};
static const NativeAbi* active_abi = nullptr;

static void nativeLog(const char*, const char* file, int line, const char* function,
                      int level, const char* format, ...) {
  char rendered[16384];
  va_list values;
  va_start(values, format);
  std::vsnprintf(rendered, sizeof(rendered), format ? format : "", values);
  va_end(values);
  // This version-pinned native event means compilation ended; progress < 1 is
  // possible on the final callback, so progress alone is not completion.
  if (line == 1203 && file && std::strcmp(file, "operator()") == 0 &&
      std::strstr(rendered, "[ve_export_impl.cpp:operator():1203][LYRA] export_callback: VE_INFO_COMPILE_DONE"))
    compile_done = true;
  if (std::strstr(rendered, "export_callback: VE_ERROR_COMPILE")) compile_error = true;
  // Nested clips restore asynchronously. Exporting after an arbitrary delay
  // can race the child timeline and produce audio-only or incomplete output.
  // This exact pinned callback occurs after the complete restore driver run.
  if (active_abi && line == active_abi->restore_line && file && std::strcmp(file, "operator()") == 0 &&
      std::strstr(rendered, active_abi->restore_event))
    restore_done = true;
  std::lock_guard<std::mutex> lock(logging_mutex);
  std::fprintf(stderr, "ENGINE [%d] %s:%d %s: %s\n", level,
               file ? file : "", line, function ? function : "", rendered);
}

static char* pinnedEngineBase() {
  Dl_info info{};
  if (!dladdr(reinterpret_cast<void*>(&lyra::Server::instance), &info)) return nullptr;
  std::ifstream input(info.dli_fname, std::ios::binary);
  if (!input) return nullptr;
  CC_SHA256_CTX state;
  CC_SHA256_Init(&state);
  char data[65536];
  while (input) {
    input.read(data, sizeof(data));
    if (input.gcount()) CC_SHA256_Update(&state, data, static_cast<CC_LONG>(input.gcount()));
  }
  unsigned char digest[CC_SHA256_DIGEST_LENGTH];
  CC_SHA256_Final(digest, &state);
  std::string hash;
  for (auto byte : digest) {
    hash += "0123456789abcdef"[byte >> 4];
    hash += "0123456789abcdef"[byte & 15];
  }
  if (input.bad()) return nullptr;
  for (const auto& profile : abi_profiles) {
    if (hash == profile.sha256) {
      active_abi = &profile;
      std::cerr << "JY_NATIVE_ABI " << profile.version << '\n';
      return reinterpret_cast<char*>(info.dli_fbase);
    }
  }
  return nullptr;
}

template <typename T> static T field(const void* pointer, size_t offset) {
  T result{};
  std::memcpy(&result, reinterpret_cast<const char*>(pointer) + offset, sizeof(T));
  return result;
}
static void checkResponse(const std::shared_ptr<lyra::RespStruct>& response, const char* stage) {
  if (!response || field<int>(response.get(), 0x40) != 0) throw std::runtime_error(stage);
}
static void pump(lyra::Server& server, int milliseconds) {
  auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(milliseconds);
  while (std::chrono::steady_clock::now() < until) { server.pumpOnce(); usleep(10000); }
}

static void configureCapturedMasks(const std::shared_ptr<void>& wrapper) {
  // getVeAdapterConfig returns the session's native-owned config. Set the
  // built-in resource root before creating any timeline; native ownership
  // manages construction, copies and destruction without guessed destructors.
  auto adapter = static_cast<lyra::wrapper::VeWrapper*>(wrapper.get())->getVeAdapterConfig();
  if (!adapter) throw std::runtime_error("native adapter configuration missing");
  // addVideo reads this hub in both reviewed libraries: 11.4.2 0x3c97268,
  // 11.5.0 0x3d7a708 (field access at 0x3d7aacc).
  if (!active_abi) throw std::runtime_error("native ABI was not selected");
  auto& hub = *reinterpret_cast<std::string*>(reinterpret_cast<char*>(adapter.get()) + active_abi->mask_hub);
  if (!hub.empty()) throw std::runtime_error("unexpected native default effect resource path");
  const std::filesystem::path root = "/Applications/VideoFusion-macOS.app/Contents/Resources/lumi_js_resources_video";
  for (const char* relative : {"config.json", "js/video/video.js", "resources/feature-mask/config.json",
                               "resources/lumi-hub/config.json"}) {
    auto file = root / relative;
    if (!std::filesystem::is_regular_file(file) || std::filesystem::is_symlink(file) ||
        std::filesystem::canonical(file) != file)
      throw std::runtime_error("signed built-in mask runtime unavailable");
  }
  // The root config selects ScriptInfoSticker. The child lumi-hub directory
  // alone selects a legacy parser incompatible with the current mask JSON.
  // No account, license, feature-gate or global application settings change.
  hub = root.string();
  std::cerr << "JY_NATIVE_MASK_RUNTIME " << hub << '\n';
}

int main(int argc, char** argv) {
  try {
    if (argc != 9) throw std::runtime_error("usage: helper timeline.json output.mp4 width height fps bitrate timeout_seconds captured_masks_0_or_1");
    const auto input = std::filesystem::canonical(argv[1]);
    const auto output = std::filesystem::absolute(argv[2]);
    if (!output.parent_path().is_absolute() || std::filesystem::exists(output) ||
        std::filesystem::is_symlink(output) || output.extension() != ".mp4")
      throw std::runtime_error("output must be a new MP4 in the owned job");
    int width = std::stoi(argv[3]), height = std::stoi(argv[4]), timeout = std::stoi(argv[7]);
    double fps = std::stod(argv[5]);
    long bitrate = std::stol(argv[6]);
    if (std::strcmp(argv[8], "0") != 0 && std::strcmp(argv[8], "1") != 0)
      throw std::runtime_error("invalid captured-mask runtime selection");
    bool captured_masks = std::strcmp(argv[8], "1") == 0;
    if (width < 16 || width > 7680 || width % 2 || height < 16 || height > 7680 || height % 2 ||
        !std::isfinite(fps) || fps < 1 || fps > 120 || bitrate < 100000 || bitrate > 200000000 ||
        timeout < 5 || timeout > 43200) throw std::runtime_error("invalid export settings");
    char* base = pinnedEngineBase();
    if (!base) throw std::runtime_error("engine differs from the supported ABI");
    std::ifstream source(input, std::ios::binary);
    std::string json((std::istreambuf_iterator<char>(source)), std::istreambuf_iterator<char>());
    if (json.empty()) throw std::runtime_error("empty timeline input");
    alarm(timeout + 15);
    lvve::Logger::getLogger()->setAlogFunction(nativeLog, nullptr);
    lvve::Logger::getLogger()->setLogLevel("debug");
    auto& server = lyra::Server::instance();
    server.startup(nullptr, [](const std::function<void()>& f) { if (f) f(); });
    long sid = server.openSession(nullptr, "isolated-local-export");
    auto session = server.getSession(sid);
    if (!session || !session->getVeWrapper()) throw std::runtime_error("native timeline engine unavailable");
    if (captured_masks) configureCapturedMasks(session->getVeWrapper());
    auto project = std::make_shared<lyra::InitReqStruct>();
    project->project_id = "isolated-local-export";
    project->timeline_name = "isolated-local-export";
    project->draft = lvve::Deserializer::deserialize_persistent_draft(json);
    if (!project->draft) throw std::runtime_error("persistent draft decode failed");
    auto initialized = ProjectClient::init(project, sid);
    checkResponse(initialized, "project initialization failed");
    long tid = field<long>(initialized.get(), 0x38);
    auto binding = std::make_shared<lyra::DraftInitReqStruct>();
    binding->tid = tid;
    binding->draft = lvve::GetDraftFromJson(json);
    if (!binding->draft) throw std::runtime_error("runtime draft decode failed");
    checkResponse(server.invoke(binding, sid), "runtime draft initialization failed");
    reinterpret_cast<void (*)(long, bool, long)>(base + active_abi->restore_draft)(sid, true, tid);
    const auto restore_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout);
    while (!restore_done && std::chrono::steady_clock::now() < restore_deadline) pump(server, 20);
    if (!restore_done) throw std::runtime_error("native timeline restoration did not finish");
    std::cerr << "JY_NATIVE_RESTORE_DONE\n";
    bool ready = false;
    session->draftTransaction([&](std::shared_ptr<lvve::Draft> draft) {
      ready = bool(draft);
      if (draft) {
        auto snapshot = output.parent_path() / "runtime-timeline.json";
        if (std::filesystem::exists(snapshot)) throw std::runtime_error("runtime snapshot already exists");
        std::ofstream saved(snapshot, std::ios::binary);
        saved << lvve::GetJsonFromDraft(draft);
        if (!saved) throw std::runtime_error("runtime snapshot write failed");
      }
    }, tid);
    if (!ready) throw std::runtime_error("session has no draft after initialization");
    void* storage = ::operator new(active_abi->export_request_size);
    std::memset(storage, 0, active_abi->export_request_size);
    // The engine's own constructor/destructor manage its packed ExportConfig.
    reinterpret_cast<void (*)(void*)>(base + active_abi->export_constructor)(storage);
    auto request = std::shared_ptr<lyra::ReqStruct>(reinterpret_cast<lyra::ReqStruct*>(storage), [](auto* p) {
      auto table = *reinterpret_cast<void***>(p);
      reinterpret_cast<void (*)(void*)>(table[0])(p);
      ::operator delete(p);
    });
    if (request->service != "ExportService" || request->api != "exportStart")
      throw std::runtime_error("unexpected native export request identity");
    request->tid = tid;
    *reinterpret_cast<std::string*>(reinterpret_cast<char*>(storage) + 0x48) = output.string();
    auto config = reinterpret_cast<char*>(storage) + 0x60;
    // ToVeCompileSetting: 11.4.2 0x3b7805c; 11.5.0 0x3c59708.
    std::memcpy(config + 0x3f, &width, sizeof(width));
    std::memcpy(config + 0x43, &height, sizeof(height));
    config[0x47] = 0;  // Native hardware-encode preference, not an encoder guarantee.
    std::memcpy(config + 0x4a, &fps, sizeof(fps));
    std::memcpy(config + 0x5e, &bitrate, sizeof(bitrate));
    config[0x279] = 1;  // Native FFmpeg MP4 writer; no external composition/remux.
    server.invokeSync(request, [](std::shared_ptr<lyra::RespStruct> response) {
      int code = response ? field<int>(response.get(), 0x40) : -999;
      if (code) callback_error = code;
    }, sid);
    auto until = std::chrono::steady_clock::now() + std::chrono::seconds(timeout);
    while (!compile_done && !compile_error && !callback_error && std::chrono::steady_clock::now() < until)
      pump(server, 20);
    bool success = compile_done && !compile_error && !callback_error;
    if (success) pump(server, 500);  // Drain the native completion/encoder-close task.
    request.reset(); binding.reset(); project.reset(); session.reset(); initialized.reset();
    server.closeSession(sid, [] {});
    pump(server, 100);
    server.shutdown();
    if (!success) throw std::runtime_error("native export failed or timed out; retained partial output is not a deliverable");
    if (!std::filesystem::exists(output) || std::filesystem::file_size(output) == 0)
      throw std::runtime_error("native completion had no nonempty output");
    std::cout << "JY_NATIVE_EXPORT_DONE\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "JY_NATIVE_EXPORT_FAILED: " << error.what() << '\n';
    return 1;
  }
}
