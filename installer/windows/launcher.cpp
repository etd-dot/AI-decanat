#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <shlobj.h>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

namespace fs = std::filesystem;

static std::wstring Quote(const fs::path& path) { return L"\"" + path.wstring() + L"\""; }

static bool KioskRequested()
{
    int count = 0;
    auto arguments = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!arguments) return false;
    bool kiosk = false;
    for (int index = 1; index < count; ++index)
        if (std::wstring_view(arguments[index]) == L"--kiosk") kiosk = true;
    LocalFree(arguments);
    return kiosk;
}

class Handle {
public:
    explicit Handle(HANDLE value) : value(value) {}
    ~Handle() { if (*this) CloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    explicit operator bool() const { return value && value != INVALID_HANDLE_VALUE; }
    HANDLE Get() const { return value; }
private:
    HANDLE value;
};

static DWORD RunAttempt(const fs::path& root, const fs::path& python,
                        std::wstring command, HANDLE input, HANDLE output)
{
    Handle job(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!job || !SetInformationJobObject(job.Get(), JobObjectExtendedLimitInformation, &limits, sizeof(limits)))
        throw 1;
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdOutput = startup.hStdError = output;
    startup.hStdInput = input;
    PROCESS_INFORMATION child{};
    if (!CreateProcessW(python.c_str(), command.data(), nullptr, nullptr, TRUE,
        CREATE_NO_WINDOW | CREATE_SUSPENDED, nullptr, root.c_str(), &startup, &child))
        throw 1;
    Handle process(child.hProcess);
    Handle thread(child.hThread);
    if (!AssignProcessToJobObject(job.Get(), process.Get())) {
        TerminateProcess(process.Get(), 1);
        WaitForSingleObject(process.Get(), 5000);
        throw 1;
    }
    if (ResumeThread(thread.Get()) == static_cast<DWORD>(-1) ||
        WaitForSingleObject(process.Get(), INFINITE) != WAIT_OBJECT_0)
        throw 1;
    DWORD result = 1;
    if (!GetExitCodeProcess(process.Get(), &result)) throw 1;
    if (!TerminateJobObject(job.Get(), 1)) throw 1;
    const ULONGLONG deadline = GetTickCount64() + 5000;
    do {
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting{};
        if (!QueryInformationJobObject(job.Get(), JobObjectBasicAccountingInformation,
            &accounting, sizeof(accounting), nullptr)) throw 1;
        if (accounting.ActiveProcesses == 0) return result;
        Sleep(50);
    } while (GetTickCount64() < deadline);
    throw 1;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR arguments, int)
{
    Handle mutex(CreateMutexW(nullptr, FALSE, L"Local\\AI-decanat-development"));
    if (!mutex || GetLastError() == ERROR_ALREADY_EXISTS) {
        MessageBoxW(nullptr, L"Приложение уже запущено.", L"ИИ деканата", MB_OK | MB_ICONINFORMATION);
        return 0;
    }
    try {
        std::vector<wchar_t> executable(32768);
        if (!GetModuleFileNameW(nullptr, executable.data(), static_cast<DWORD>(executable.size()))) throw 1;
        const fs::path root = fs::path(executable.data()).parent_path();
        const fs::path python = root / L"runtime\\python.exe";
        const fs::path script = root / L"tools\\installed_desktop.py";
        if (!fs::is_regular_file(python) || !fs::is_regular_file(script)) throw 1;
        PWSTR known = nullptr;
        if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &known))) throw 1;
        const fs::path data = fs::path(known) / L"AI-decanat-development";
        CoTaskMemFree(known);
        fs::create_directories(data);
        const fs::path log = data / (L"launcher-" + std::to_wstring(GetCurrentProcessId()) + L".log");
        SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
        Handle output(CreateFileW(log.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &security,
            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr));
        Handle input(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
            &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
        if (!output || !input) throw 1;
        // All dependencies are application-local; no system PATH changes.
        DWORD pathSize = GetEnvironmentVariableW(L"PATH", nullptr, 0);
        std::vector<wchar_t> oldPath(pathSize ? pathSize : 1);
        if (pathSize) GetEnvironmentVariableW(L"PATH", oldPath.data(), pathSize);
        const std::wstring processPath = (root / L"runtime").wstring() + L";" + oldPath.data();
        SetEnvironmentVariableW(L"PATH", processPath.c_str());
        SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
        std::wstring command = Quote(python) + L" -I -B -X utf8 " + Quote(script) + L" " + arguments;
        constexpr DWORD RetryableError = 75;
        constexpr int MaxAttempts = 3;
        const bool kiosk = KioskRequested();
        DWORD result = 0;
        for (int attempt = 1; kiosk || attempt <= MaxAttempts; ++attempt) {
            if (GetSystemMetrics(SM_SHUTTINGDOWN)) return 0;
            if (attempt > 1) Sleep(attempt == 2 ? 1500 : 4000);
            if (GetSystemMetrics(SM_SHUTTINGDOWN)) return 0;
            const std::string marker = "\r\n=== Decanat attempt " + std::to_string(attempt) + " ===\r\n";
            DWORD written = 0;
            WriteFile(output.Get(), marker.data(), static_cast<DWORD>(marker.size()), &written, nullptr);
            result = RunAttempt(root, python, command, input.Get(), output.Get());
            if (!kiosk && result != RetryableError) break;
        }
        if (GetSystemMetrics(SM_SHUTTINGDOWN) || result == 0xC000013A) return 0;
        if (result) {
            const std::wstring message = L"Приложение остановлено. Запустите его заново или передайте журнал сотруднику:\n" + log.wstring();
            MessageBoxW(nullptr, message.c_str(), L"ИИ деканата", MB_OK | MB_ICONERROR);
        }
        return static_cast<int>(result);
    } catch (...) {
        MessageBoxW(nullptr, L"Не удалось запустить приложение. Проверьте установку и свободное место на диске.",
            L"ИИ деканата", MB_OK | MB_ICONERROR);
        return 1;
    }
}
