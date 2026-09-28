#pragma once

#include <string>

// SPDX-License-Identifier: MIT
// Copyright 2026 wenshui330. See ../licenses/jy-draftc-MIT.txt.
// The public method declarations below are adapted from the MIT-licensed
// jy-draftc macOS sample.  The implementation is supplied at runtime by the
// user's own JianYing installation; this package does not redistribute it.
namespace lvve {
class EncryptUtils {
 public:
  bool isEnable();
  void enable(bool enabled);
  std::string encrypt(const std::string& input);
  std::string decrypt(const std::string& input, const std::string& parameters);
  std::string decrypt(const std::string& input, const std::string& parameters,
                      bool& valid);
};
}  // namespace lvve
