#include <cerrno>
#include <climits>
#include <cstdint>
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>

#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "EncryptUtil.h"

namespace {

namespace fs = std::filesystem;

constexpr std::uint64_t kMaxInputBytes = 256ULL * 1024ULL * 1024ULL;
constexpr std::uint64_t kMaxOutputBytes = 512ULL * 1024ULL * 1024ULL;
constexpr std::size_t kReadChunkBytes = 64U * 1024U;

[[noreturn]] void ThrowErrno(const std::string& message) {
  const int saved_errno = errno;
  throw std::system_error(saved_errno, std::generic_category(), message);
}

class FileDescriptor {
 public:
  explicit FileDescriptor(int fd = -1) noexcept : fd_(fd) {}

  FileDescriptor(const FileDescriptor&) = delete;
  FileDescriptor& operator=(const FileDescriptor&) = delete;

  FileDescriptor(FileDescriptor&& other) noexcept : fd_(other.Release()) {}

  FileDescriptor& operator=(FileDescriptor&& other) noexcept {
    if (this != &other) {
      CloseBestEffort();
      fd_ = other.Release();
    }
    return *this;
  }

  ~FileDescriptor() { CloseBestEffort(); }

  int get() const noexcept { return fd_; }

  void CloseChecked(const std::string& message) {
    if (fd_ < 0) {
      return;
    }
    const int fd = Release();
    if (::close(fd) != 0) {
      ThrowErrno(message);
    }
  }

 private:
  int Release() noexcept {
    const int fd = fd_;
    fd_ = -1;
    return fd;
  }

  void CloseBestEffort() noexcept {
    if (fd_ < 0) {
      return;
    }
    const int saved_errno = errno;
    const int fd = Release();
    (void)::close(fd);
    errno = saved_errno;
  }

  int fd_;
};

struct stat LstatChecked(const std::string& path,
                         const std::string& message) {
  struct stat status {};
  while (::lstat(path.c_str(), &status) != 0) {
    if (errno == EINTR) {
      continue;
    }
    ThrowErrno(message + ": " + path);
  }
  return status;
}

struct stat FstatChecked(int fd, const std::string& message) {
  struct stat status {};
  while (::fstat(fd, &status) != 0) {
    if (errno == EINTR) {
      continue;
    }
    ThrowErrno(message);
  }
  return status;
}

bool IsSameObject(const struct stat& left, const struct stat& right) {
  return left.st_dev == right.st_dev && left.st_ino == right.st_ino;
}

std::uint64_t CheckedRegularFileSize(const struct stat& status,
                                     std::uint64_t maximum,
                                     const std::string& label) {
  if (!S_ISREG(status.st_mode)) {
    throw std::runtime_error(label +
                             " is not a regular file; symlinks and special "
                             "files are refused");
  }
  if (status.st_size < 0 ||
      static_cast<std::uint64_t>(status.st_size) > maximum) {
    throw std::runtime_error(label + " exceeds the size limit of " +
                             std::to_string(maximum) + " bytes");
  }
  return static_cast<std::uint64_t>(status.st_size);
}

int OpenInputNoFollow(const std::string& path) {
  for (;;) {
    const int fd = ::open(path.c_str(),
                          O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
    if (fd >= 0) {
      return fd;
    }
    if (errno != EINTR) {
      ThrowErrno("cannot securely open input: " + path);
    }
  }
}

std::string ReadAll(const std::string& path) {
  const struct stat path_status =
      LstatChecked(path, "cannot inspect input");
  const std::uint64_t path_size =
      CheckedRegularFileSize(path_status, kMaxInputBytes, "input");

  FileDescriptor input(OpenInputNoFollow(path));
  const struct stat opened_status =
      FstatChecked(input.get(), "cannot inspect opened input");
  const std::uint64_t opened_size =
      CheckedRegularFileSize(opened_status, kMaxInputBytes, "opened input");
  if (!IsSameObject(path_status, opened_status)) {
    throw std::runtime_error("input changed while it was being opened");
  }
  if (path_size != opened_size) {
    throw std::runtime_error("input size changed while it was being opened");
  }

  std::string content;
  content.reserve(static_cast<std::size_t>(opened_size));
  char buffer[kReadChunkBytes];
  for (;;) {
    const ssize_t bytes_read = ::read(input.get(), buffer, sizeof(buffer));
    if (bytes_read < 0) {
      if (errno == EINTR) {
        continue;
      }
      ThrowErrno("failed reading input: " + path);
    }
    if (bytes_read == 0) {
      break;
    }

    const std::uint64_t chunk_size =
        static_cast<std::uint64_t>(bytes_read);
    if (chunk_size > kMaxInputBytes - content.size()) {
      throw std::runtime_error("input grew beyond the size limit of " +
                               std::to_string(kMaxInputBytes) + " bytes");
    }
    content.append(buffer, static_cast<std::size_t>(bytes_read));
  }

  const struct stat final_status =
      FstatChecked(input.get(), "cannot re-inspect opened input");
  const std::uint64_t final_size =
      CheckedRegularFileSize(final_status, kMaxInputBytes, "opened input");
  if (!IsSameObject(opened_status, final_status) ||
      final_size != opened_size || content.size() != opened_size) {
    throw std::runtime_error("input changed while it was being read");
  }

  input.CloseChecked("failed closing input: " + path);
  return content;
}

struct OutputPath {
  std::string parent;
  std::string name;
};

OutputPath SplitOutputPath(const std::string& path) {
  if (path.empty()) {
    throw std::runtime_error("output path is empty");
  }

  const std::string::size_type slash = path.find_last_of('/');
  OutputPath result;
  if (slash == std::string::npos) {
    result.parent = ".";
    result.name = path;
  } else if (slash == 0) {
    result.parent = "/";
    result.name = path.substr(1);
  } else {
    result.parent = path.substr(0, slash);
    result.name = path.substr(slash + 1);
  }

  if (result.name.empty() || result.name == "." || result.name == "..") {
    throw std::runtime_error("output path must name a new file");
  }
  return result;
}

int OpenDirectory(const char* path, const std::string& message) {
  for (;;) {
    const int fd =
        ::open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd >= 0) {
      return fd;
    }
    if (errno != EINTR) {
      ThrowErrno(message);
    }
  }
}

int OpenDirectoryAt(int parent_fd, const std::string& component,
                    const std::string& parent_path) {
  for (;;) {
    const int fd = ::openat(parent_fd, component.c_str(),
                            O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd >= 0) {
      return fd;
    }
    if (errno != EINTR) {
      ThrowErrno("cannot securely open output parent component '" + component +
                 "' in " + parent_path);
    }
  }
}

FileDescriptor OpenVerifiedParent(const std::string& parent_path) {
  const struct stat path_status =
      LstatChecked(parent_path, "cannot inspect output parent");
  if (!S_ISDIR(path_status.st_mode)) {
    throw std::runtime_error(
        "output parent is not a real directory; symlinks are refused: " +
        parent_path);
  }

  const bool absolute = !parent_path.empty() && parent_path.front() == '/';
  FileDescriptor current(OpenDirectory(
      absolute ? "/" : ".", "cannot open output path traversal root"));

  std::size_t cursor = absolute ? 1U : 0U;
  while (cursor < parent_path.size()) {
    while (cursor < parent_path.size() && parent_path[cursor] == '/') {
      ++cursor;
    }
    if (cursor == parent_path.size()) {
      break;
    }
    const std::size_t end = parent_path.find('/', cursor);
    const std::string component = parent_path.substr(
        cursor, end == std::string::npos ? std::string::npos : end - cursor);
    cursor = end == std::string::npos ? parent_path.size() : end + 1U;
    if (component.empty() || component == ".") {
      continue;
    }

    FileDescriptor next(
        OpenDirectoryAt(current.get(), component, parent_path));
    current.CloseChecked("failed closing traversed output directory");
    current = std::move(next);
  }

  const struct stat opened_status =
      FstatChecked(current.get(), "cannot inspect opened output parent");
  if (!S_ISDIR(opened_status.st_mode) ||
      !IsSameObject(path_status, opened_status)) {
    throw std::runtime_error("output parent changed while it was being opened");
  }
  return current;
}

int CreateOutputAt(int parent_fd, const std::string& name,
                   const std::string& full_path) {
  for (;;) {
    const int fd = ::openat(parent_fd, name.c_str(),
                            O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW |
                                O_CLOEXEC,
                            S_IRUSR | S_IWUSR);
    if (fd >= 0) {
      return fd;
    }
    if (errno == EINTR) {
      continue;
    }
    if (errno == EEXIST) {
      throw std::runtime_error("refusing to overwrite existing output: " +
                               full_path);
    }
    ThrowErrno("cannot securely create output: " + full_path);
  }
}

void FsyncChecked(int fd, const std::string& message) {
  while (::fsync(fd) != 0) {
    if (errno == EINTR) {
      continue;
    }
    ThrowErrno(message);
  }
}

struct stat FstatAtNoFollowChecked(int parent_fd, const std::string& name,
                                   const std::string& message) {
  struct stat status {};
  while (::fstatat(parent_fd, name.c_str(), &status, AT_SYMLINK_NOFOLLOW) != 0) {
    if (errno == EINTR) {
      continue;
    }
    ThrowErrno(message);
  }
  return status;
}

void WriteNew(const std::string& path, const std::string& content) {
  if (content.size() > kMaxOutputBytes) {
    throw std::runtime_error("output exceeds the size limit of " +
                             std::to_string(kMaxOutputBytes) + " bytes");
  }

  const OutputPath output_path = SplitOutputPath(path);
  FileDescriptor parent = OpenVerifiedParent(output_path.parent);
  FileDescriptor output(
      CreateOutputAt(parent.get(), output_path.name, path));

  const struct stat created_status =
      FstatChecked(output.get(), "cannot inspect newly created output");
  if (!S_ISREG(created_status.st_mode)) {
    throw std::runtime_error("newly created output is not a regular file");
  }
  if (::fchmod(output.get(), S_IRUSR | S_IWUSR) != 0) {
    ThrowErrno("cannot set output permissions to 0600: " + path);
  }

  std::size_t offset = 0;
  while (offset < content.size()) {
    const ssize_t bytes_written =
        ::write(output.get(), content.data() + offset, content.size() - offset);
    if (bytes_written < 0) {
      if (errno == EINTR) {
        continue;
      }
      ThrowErrno("failed writing output: " + path);
    }
    if (bytes_written == 0) {
      throw std::runtime_error("write returned zero before output completed: " +
                               path);
    }
    offset += static_cast<std::size_t>(bytes_written);
  }

  FsyncChecked(output.get(), "failed syncing output: " + path);
  const struct stat final_status =
      FstatChecked(output.get(), "cannot re-inspect output: " + path);
  if (!S_ISREG(final_status.st_mode) ||
      !IsSameObject(created_status, final_status) || final_status.st_size < 0 ||
      static_cast<std::uint64_t>(final_status.st_size) != content.size()) {
    throw std::runtime_error("output verification failed: " + path);
  }

  const struct stat linked_status = FstatAtNoFollowChecked(
      parent.get(), output_path.name,
      "cannot verify created output directory entry: " + path);
  if (!S_ISREG(linked_status.st_mode) ||
      !IsSameObject(created_status, linked_status)) {
    throw std::runtime_error("output directory entry changed while writing: " +
                             path);
  }

  output.CloseChecked("failed closing output: " + path);
  FsyncChecked(parent.get(), "failed syncing output parent: " +
                                    output_path.parent);
  parent.CloseChecked("failed closing output parent: " + output_path.parent);
}

void WriteAllToFd(int fd, const std::string& content) {
  if (content.size() > kMaxOutputBytes) {
    throw std::runtime_error("decrypted output exceeds the size limit of " +
                             std::to_string(kMaxOutputBytes) + " bytes");
  }
  if (fd < 3) {
    throw std::runtime_error(
        "decrypt-fd requires a caller-owned descriptor number >= 3");
  }
  const struct stat status = FstatChecked(fd, "cannot inspect output fd");
  if (!S_ISFIFO(status.st_mode) && !S_ISSOCK(status.st_mode)) {
    throw std::runtime_error(
        "decrypt-fd output must be an anonymous pipe or socket");
  }
  std::size_t offset = 0;
  while (offset < content.size()) {
    const ssize_t bytes_written =
        ::write(fd, content.data() + offset, content.size() - offset);
    if (bytes_written < 0) {
      if (errno == EINTR) {
        continue;
      }
      ThrowErrno("failed writing decrypted output fd");
    }
    if (bytes_written == 0) {
      throw std::runtime_error(
          "write returned zero before decrypted output completed");
    }
    offset += static_cast<std::size_t>(bytes_written);
  }
}

int ParsePipeFd(const char* text, const std::string& command) {
  const std::string raw(text == nullptr ? "" : text);
  std::size_t parsed = 0;
  const long long value = std::stoll(raw, &parsed, 10);
  if (parsed != raw.size() || value < 3 || value > INT_MAX) {
    throw std::runtime_error(command +
                             " descriptor must be an integer >= 3");
  }
  return static_cast<int>(value);
}

void PreparePipeFd(int fd, bool for_writing, const std::string& command) {
  const struct stat status = FstatChecked(fd, "cannot inspect " + command + " fd");
  if (!S_ISFIFO(status.st_mode) && !S_ISSOCK(status.st_mode)) {
    throw std::runtime_error(command +
                             " fd must be an anonymous pipe or socket");
  }
  const int access_flags = ::fcntl(fd, F_GETFL);
  if (access_flags < 0) {
    ThrowErrno("cannot inspect " + command + " fd access mode");
  }
  const int access_mode = access_flags & O_ACCMODE;
  if ((for_writing && access_mode == O_RDONLY) ||
      (!for_writing && access_mode == O_WRONLY)) {
    throw std::runtime_error(command + " fd has the wrong access direction");
  }
  const int descriptor_flags = ::fcntl(fd, F_GETFD);
  if (descriptor_flags < 0) {
    ThrowErrno("cannot inspect " + command + " fd flags");
  }
  if (::fcntl(fd, F_SETFD, descriptor_flags | FD_CLOEXEC) != 0) {
    ThrowErrno("cannot set close-on-exec for " + command + " fd");
  }
}

std::string ReadAllFromFd(int fd) {
  if (fd < 3) {
    throw std::runtime_error(
        "encrypt-fd requires a caller-owned descriptor number >= 3");
  }
  const struct stat status = FstatChecked(fd, "cannot inspect input fd");
  if (!S_ISFIFO(status.st_mode) && !S_ISSOCK(status.st_mode)) {
    throw std::runtime_error(
        "encrypt-fd input must be an anonymous pipe or socket");
  }
  std::string content;
  char buffer[kReadChunkBytes];
  for (;;) {
    const ssize_t bytes_read = ::read(fd, buffer, sizeof(buffer));
    if (bytes_read < 0) {
      if (errno == EINTR) {
        continue;
      }
      ThrowErrno("failed reading plaintext input fd");
    }
    if (bytes_read == 0) {
      break;
    }
    const std::uint64_t chunk_size =
        static_cast<std::uint64_t>(bytes_read);
    if (chunk_size > kMaxInputBytes - content.size()) {
      throw std::runtime_error(
          "plaintext input grew beyond the size limit of " +
          std::to_string(kMaxInputBytes) + " bytes");
    }
    content.append(buffer, static_cast<std::size_t>(bytes_read));
  }
  if (content.empty()) {
    throw std::runtime_error("encrypt-fd received empty plaintext");
  }
  return content;
}

}  // namespace

int main(int argc, char** argv) {
  const bool decrypt_fd =
      argc == 4 && std::string(argv[1]) == "decrypt-fd";
  const bool encrypt_fd =
      argc == 4 && std::string(argv[1]) == "encrypt-fd";
  if (argc != 4) {
    std::cerr << "usage: jy14_codec_hardened "
                 "decrypt|encrypt INPUT OUTPUT\n"
                 "       jy14_codec_hardened decrypt-fd INPUT FD\n"
                 "       jy14_codec_hardened encrypt-fd FD OUTPUT\n";
    return 64;
  }

  const std::string command = argv[1];
  if (command != "decrypt" && command != "encrypt" && !decrypt_fd &&
      !encrypt_fd) {
    std::cerr << "error: unknown command: " << command << "\n";
    return 64;
  }

  try {
    // Resolve both paths before invoking the vendor library so a relative path
    // cannot be reinterpreted if library code changes the process directory.
    std::string input;
    std::string input_path;
    std::string output_path;
    int pipe_fd = -1;
    if (encrypt_fd) {
      pipe_fd = ParsePipeFd(argv[2], "encrypt-fd");
      PreparePipeFd(pipe_fd, false, "encrypt-fd input");
      output_path = fs::absolute(argv[3]).string();
      input = ReadAllFromFd(pipe_fd);
    } else {
      input_path = fs::absolute(argv[2]).string();
      if (decrypt_fd) {
        pipe_fd = ParsePipeFd(argv[3], "decrypt-fd");
        PreparePipeFd(pipe_fd, true, "decrypt-fd output");
      } else {
        output_path = fs::absolute(argv[3]).string();
      }
      input = ReadAll(input_path);
    }

    lvve::EncryptUtils codec;
    codec.enable(true);

    if (command == "decrypt" || decrypt_fd) {
      bool valid = false;
      const std::string plaintext = codec.decrypt(input, "{}", valid);
      if (!valid || plaintext.empty()) {
        throw std::runtime_error("decrypt failed or produced empty content");
      }
      if (decrypt_fd) {
        WriteAllToFd(pipe_fd, plaintext);
        std::cerr << "mode=decrypt-fd input_bytes=" << input.size()
                  << " output_bytes=" << plaintext.size() << "\n";
        return 0;
      }
      WriteNew(output_path, plaintext);
      std::cout << "mode=decrypt input_bytes=" << input.size()
                << " output_bytes=" << plaintext.size()
                << " output=" << output_path << "\n";
      return 0;
    }

    const std::string ciphertext = codec.encrypt(input);
    if (ciphertext.empty()) {
      throw std::runtime_error("encrypt produced empty content");
    }
    WriteNew(output_path, ciphertext);
    std::cout << "mode=" << (encrypt_fd ? "encrypt-fd" : "encrypt")
              << " input_bytes=" << input.size()
              << " output_bytes=" << ciphertext.size()
              << " output=" << output_path << "\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "error: " << error.what() << "\n";
    return 1;
  }
}
