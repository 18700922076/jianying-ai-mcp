// Minimal jy-draftc-compatible decrypt helper for jianying-ai-mcp.
// Adapted from jy-draftc, Copyright (c) 2026 wenshui330, MIT License.
// SPDX-License-Identifier: MIT

#include <windows.h>

#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;

struct MsvcString {
    union {
        char small[16];
        char *ptr;
    } data{};
    unsigned long long size = 0;
    unsigned long long capacity = 15;
};

using DecryptFn = MsvcString *(*)(void *, MsvcString *, const MsvcString *, const MsvcString *, bool *);

static constexpr const char *kDecrypt =
    "?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z";

struct StringArg {
    std::string storage;
    MsvcString value;

    explicit StringArg(std::string text) : storage(std::move(text)) {
        value.size = storage.size();
        if (storage.size() < 16) {
            std::memset(value.data.small, 0, sizeof(value.data.small));
            std::memcpy(value.data.small, storage.data(), storage.size());
        } else {
            value.capacity = storage.size();
            value.data.ptr = storage.data();
        }
    }
};

static std::string read_all(const fs::path &path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot open input");
    return {std::istreambuf_iterator<char>(stream), {}};
}

static void write_all(const fs::path &path, const std::string &data) {
    std::ofstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot open output");
    stream.write(data.data(), static_cast<std::streamsize>(data.size()));
    if (!stream) throw std::runtime_error("cannot write output");
}

static void configure_dll_search(const fs::path &install_dir) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX);
    std::wstring old_path;
    DWORD needed = GetEnvironmentVariableW(L"PATH", nullptr, 0);
    if (needed) {
        old_path.resize(needed);
        DWORD copied = GetEnvironmentVariableW(L"PATH", old_path.data(), needed);
        old_path.resize(copied);
    }
    std::wstring merged = install_dir.wstring() + L";" + old_path;
    SetEnvironmentVariableW(L"PATH", merged.c_str());
    SetCurrentDirectoryW(install_dir.wstring().c_str());
    SetDefaultDllDirectories(
        LOAD_LIBRARY_SEARCH_APPLICATION_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    AddDllDirectory(install_dir.wstring().c_str());
}

int wmain(int argc, wchar_t **argv) {
    if (argc != 6 || std::wstring(argv[1]) != L"--decrypt" || std::wstring(argv[4]) != L"--install-dir") {
        std::cerr << "usage: jy-draftc --decrypt <input> <output> --install-dir <Jianying-version-dir>\n";
        return 64;
    }

    try {
        fs::path input = fs::absolute(argv[2]);
        fs::path output = fs::absolute(argv[3]);
        fs::path install_dir = fs::absolute(argv[5]);
        fs::path dll_path = install_dir / L"videoeditor.dll";
        if (!fs::is_regular_file(dll_path)) throw std::runtime_error("videoeditor.dll not found");

        configure_dll_search(install_dir);
        DWORD flags = LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_APPLICATION_DIR |
                      LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS;
        HMODULE module = LoadLibraryExW(dll_path.wstring().c_str(), nullptr, flags);
        if (!module) throw std::runtime_error("cannot load videoeditor.dll");
        FARPROC symbol = GetProcAddress(module, kDecrypt);
        if (!symbol) throw std::runtime_error("EncryptUtils::decrypt export not found; Jianying version is incompatible");
        DecryptFn decrypt{};
        static_assert(sizeof(decrypt) == sizeof(symbol));
        std::memcpy(&decrypt, &symbol, sizeof(decrypt));

        StringArg encrypted(read_all(input));
        StringArg params("{}");
        MsvcString result{};
        bool ok = false;
        decrypt(nullptr, &result, &encrypted.value, &params.value, &ok);
        const char *data = result.capacity < 16 ? result.data.small : result.data.ptr;
        if (!ok || !data || result.size == 0 || result.size > (1ull << 32)) {
            throw std::runtime_error("decrypt returned no valid content");
        }
        write_all(output, std::string(data, data + result.size));
        std::cout << "ok\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
}
