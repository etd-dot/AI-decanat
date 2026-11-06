#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shlobj.h>
#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

static std::wstring Quote(const fs::path& path) { return L"\"" + path.wstring() + L"\""; }

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR arguments, int)
{
    HANDLE mutex = CreateMutexW(nullptr, FALSE, L"Local\\AI-decanat-development");
    if (!mutex || GetLastError() == ERROR_ALREADY_EXISTS) {
        MessageBoxW(nullptr, L"Приложение уже запущено.", L"ИИ деканата", MB_OK | MB_ICONINFORMATION);
        if (mutex) CloseHandle(mutex);
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
        HANDLE output = CreateFileW(log.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &security,
            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (output == INVALID_HANDLE_VALUE) throw 1;
        HANDLE job = CreateJobObjectW(nullptr, nullptr);
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (!job || !SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
            CloseHandle(output);
            if (job) CloseHandle(job);
            throw 1;
        }
        // All dependencies are application-local; no system PATH changes.
        DWORD pathSize = GetEnvironmentVariableW(L"PATH", nullptr, 0);
        std::vector<wchar_t> oldPath(pathSize ? pathSize : 1);
        if (pathSize) GetEnvironmentVariableW(L"PATH", oldPath.data(), pathSize);
        const std::wstring processPath = (root / L"runtime").wstring() + L";" + oldPath.data();
        SetEnvironmentVariableW(L"PATH", processPath.c_str());
        SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
        std::wstring command = Quote(python) + L" -I -B -X utf8 " + Quote(script) + L" " + arguments;
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdOutput = startup.hStdError = output;
        startup.hStdInput = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
            &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        PROCESS_INFORMATION process{};
        const BOOL started = CreateProcessW(python.c_str(), command.data(), nullptr, nullptr, TRUE,
            CREATE_NO_WINDOW | CREATE_SUSPENDED, nullptr, root.c_str(), &startup, &process);
        CloseHandle(output);
        if (startup.hStdInput != INVALID_HANDLE_VALUE) CloseHandle(startup.hStdInput);
        if (!started) { CloseHandle(job); throw 1; }
        if (!AssignProcessToJobObject(job, process.hProcess)) {
            TerminateProcess(process.hProcess, 1);
            CloseHandle(process.hThread); CloseHandle(process.hProcess); CloseHandle(job);
            throw 1;
        }
        ResumeThread(process.hThread);
        CloseHandle(process.hThread);
        WaitForSingleObject(process.hProcess, INFINITE);
        DWORD result = 1;
        GetExitCodeProcess(process.hProcess, &result);
        CloseHandle(process.hProcess);
        // Closing the job also cleans up descendants after abnormal exit.
        CloseHandle(job);
        CloseHandle(mutex);
        if (result) {
            const std::wstring message = L"Не удалось запустить приложение. Журнал для сотрудника:\n" + log.wstring();
            MessageBoxW(nullptr, message.c_str(), L"ИИ деканата", MB_OK | MB_ICONERROR);
        }
        return static_cast<int>(result);
    } catch (...) {
        CloseHandle(mutex);
        MessageBoxW(nullptr, L"Не удалось запустить приложение. Проверьте установку и свободное место на диске.",
            L"ИИ деканата", MB_OK | MB_ICONERROR);
        return 1;
    }
}
