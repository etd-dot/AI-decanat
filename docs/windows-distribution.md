# Самостоятельная Windows-поставка — F-04

## Комплект 2D

В 2D-комплект входят `Decanat.exe`, окно Tk, атлас аватара, Python, локальные модели речи и поиска, DLL эхоподавления и Codex. Обычный запуск выполняется через `Decanat.exe`. Профиль сотрудника и журнал хранятся в `%LOCALAPPDATA%\AI-decanat-2D`.

Для сборки 2D из Developer PowerShell подготовьте зависимости Python, модели из `tools/model-sources.json`, `.tools/native/bin/decanat_audio.dll`, Codex и Inno Setup. Передайте путь к папке x64 CRT установленного MSVC.

```powershell
& .venv/Scripts/python.exe tools/install_codex_runtime.py
& .venv/Scripts/python.exe tools/build_windows_distribution.py --development --presentation 2d --output dist/windows-2d --crt-dir $crtDirectory
& .venv/Scripts/python.exe tools/verify_windows_distribution.py dist/windows-2d
$env:DECANAT_PAYLOAD_ROOT = (Resolve-Path dist/windows-2d).Path
$env:DECANAT_INSTALLER_ROOT = Join-Path (Get-Location) dist/installer-2d
& .tools/inno-7.1.1/ISCC.exe /Q /DVoicePresentation=1 installer/windows/development.iss
```

При переносе установщика скопируйте весь выходной каталог. При первом запуске задайте пароль сотрудника, загрузите базу и проверьте сервис ответов и звук. Настройка терминала описана в разделе [киоска 2D](#киоск-2d-на-windows-11-pro).

## История поставки 3D

Разработческий комплект для Windows 11 x64 содержит настоящий packaged Unreal-аватар, локальный Python runtime 3.12.16, 81 runtime-пакет из установленного lockfile-окружения, WebRTC AEC DLL, локальные модели Whisper/Silero/BGE-M3 и отдельный официальный Codex 0.166.0. Python, Unreal Editor, VS Code и Visual Studio со стороны получателя не требуются. Совместимые NVIDIA GPU/драйвер и вход сотрудника в сервис ответов остаются необходимыми.

Это **демонстрационная поставка**, с явно вымышленной базой справок и часов приёма. Production qualification остаётся false: окончательные права на модели, чистая Windows и системная приёмка не закрыты. `build_windows_distribution.py` без `--development` отказывается создавать релиз. Утверждённые внешность и голос сохранены.

После очистки 2026-11-06 промежуточный `dist/windows-development-f04-01`, отклонённые установщики и staging удалены. Сохранены один проверенный installer `dist/installer-development-f04-05` и рабочий Unreal runtime `.runtime/unreal/package-with-ace`. Не создавать полные установочные копии для каждого изменения: собирать их при необходимости проверки поставки, после успешного прогона убирать временные payload/staging и предыдущие варианты. Исходники, модели, настройки, автосохранения и пользовательские базы сохранять.

## Состав и изоляция

- `Decanat.exe` — собственный небольшой Windows GUI-запускатель, статически связанный MSVC. Он не открывает консоль; Windows Job Object завершает дочерние процессы при аварийном выходе запускателя.
- `runtime/` — отдельный интерпретатор и только замыкание runtime-зависимостей с optional-группами speech/models. Включены необходимые DLL и лицензионные файлы пакетов. Разработческие pytest/Ruff/CMake/Meson/Ninja не копируются.
- `Avatar/` — та же скомпилированная Unreal-сборка, которая прошла runtime-проверки. Отладочные символы, `Saved`, `Intermediate` и `DerivedDataCache` исключены; сохранённые настройки и журналы разработчика не переносятся.
- `models/` — только файлы, перечисленные в `tools/model-sources.json`; их SHA проверяются перед копированием и при первичной подготовке профиля. Модели не скачиваются при разговоре.
- `codex/` — самостоятельный пакет издателя из npm, закреплённый SHA-512, с Apache-2.0 LICENSE из соответствующего исходного тега. Аккаунт, токены и профиль `.codex` не копируются. Реальный структурированный ответ отдельного клиента проверен.
- `notices/` — уведомления Python-пакетов, native AEC/Abseil, NVIDIA и компилятора установщика; дополнительные файлы лицензий сохраняются внутри соответствующих компонентов.

Запуск backend сохраняет isolated mode и не создаёт консольных окон. Имена переменных окружения Windows сопоставляются без учёта регистра: системные пути нужны отдельному Codex-клиенту; API-ключ в подписочный процесс не передаётся. По актуальной [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference) для запрета локальных изображений используется `features.view_image=false`.

## Сборка на этом стенде

Сначала должны пройти настоящие проверки новой сборки аватара. Упаковщик сверяет SHA игрового EXE со свежим отчётом и проверяет прохождение сценариев ACE. Пример команд выполняется из Developer PowerShell с MSVC 14.38.

```powershell
& powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action Package -WithACE
& .venv/Scripts/python.exe tools/verify_packaged_motion.py .runtime/unreal/package-with-ace/Windows/Decanat/Binaries/Win64/Decanat.exe --real-face --play-output --cancel-preparation
& .venv/Scripts/python.exe tools/install_codex_runtime.py
& .venv/Scripts/python.exe tools/build_windows_distribution.py --development --runtime-report .runtime/unreal/packaged-motion-verification.json --output dist/windows-development-new --crt-dir C:/Games/BuildTools2022/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT
& .venv/Scripts/python.exe tools/verify_windows_distribution.py dist/windows-development-new
$env:DECANAT_PAYLOAD_ROOT=(Resolve-Path dist/windows-development-new).Path
$env:DECANAT_INSTALLER_ROOT=Join-Path (Get-Location) dist/installer-development-new
& .tools/inno-7.1.1/ISCC.exe /Q installer/windows/development.iss
& .venv/Scripts/python.exe tools/verify_windows_installer.py dist/installer-development-new/AI-decanat-development-setup.exe
```

Существующий payload не перезаписывается: для новой сборки задаётся новый каталог. Отдельный Codex также не заменяется повторным запуском install-скрипта. Inno Setup 7.1.1 получен из официального релиза; до запуска проверены SHA-256 `8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0` и цифровая подпись Pyrsys B.V. Проверка подписи компилятора не означает, что собственный установщик проекта подписан.

Проверенный комплект — `dist/installer-development-f04-05/`: EXE и восемь соседних `*-N.bin`, около 11 ГБ суммарно. В development-версии сжатие отключено для быстрого повторения испытаний. При переносе нужен **весь каталог**, части должны оставаться рядом. Установка выполняется для текущего пользователя, без изменения системного PATH и без запроса административных прав.

## Проверки

`verify_windows_distribution.py` запускает **копию** интерпретатора с `-I -B`, убирает средства разработки из PATH, включает offline mode и намеренно подставляет PYTHONPATH рабочей папки. Проверяется, что импортирован собственный установленный backend. В новом профиле проверяются SHA моделей, строится BGE-индекс и реально выполняются локальные TTS/ASR/VAD/AEC. Микрофон и аудиовыход не открываются. Этот прогон уже прошёл; обычный Windows EXE-запускатель тоже прошёл offline-проверку.

`verify_windows_installer.py` устанавливает приложение в новый каталог `.runtime` с пробелами и кириллицей, проверяет установленный native-запускатель и автономную речь, затем удаляет программу штатным uninstaller. Отдельно проверяется сохранность данных пользователя. Он не удаляет рабочую папку проекта и не меняет активную студенческую сессию.

Последний прогон прошёл: настоящий установленный EXE, модели, AEC и PortAudio работают; все шесть файлов профиля сохранились побайтово после удаления. В этом процессе найден и исправлен PyTorch filename-reader для кириллицы: TTS загружается из Python binary stream, повторная реплика проверена отдельно. Запускатель и backend включают UTF-8 явно. Инвентарь, SHA и границы — [windows-distribution.json](../assets/manifest/windows-distribution.json).

Это испытания на доступном ПК, где средства разработки установлены. Они подтверждают изоляцию путей и комплектование runtime, но **не заменяют чистую Windows**, проверку других GPU/устройств или восьмичасовой прогон.

## Данные сотрудника и следующая работа

Настройки, база и журнал находятся в `%LOCALAPPDATA%\AI-decanat-development`, отдельно от каталога программы. При удалении приложения они сохраняются. Новый профиль получает параметры поставляемых моделей после проверки SHA-256. При первом запуске нажмите Ctrl+Alt+A и задайте пароль. В интерфейсе сотрудника загрузите рабочий ZIP вуза, проверьте выбранный сервис ответов и настройте микрофон и динамики. Вымышленную базу можно установить отдельной кнопкой для демонстрации. Существующие пароль и база сохраняются при обновлении. Если в старом профиле сохранился `initial-password.txt`, используйте прежний пароль для входа, смените его в настройках и удалите файл.

Windows-запускатель делает до трёх попыток при аварийном выходе backend или Unreal с паузами 1.5 и 4 секунды. Между попытками Windows Job Object завершает процессы прежнего запуска. При штатном выходе приложение закрывается. При исчерпании попыток окно показывает путь к журналу запуска. Каждая попытка создаёт свой IPC-файл и новые токены, а новый разговор начинается с пустого контекста.

## Киоск 2D на Windows 11 Pro

Терминал использует отдельную локальную учётную запись `DecanatKiosk`. Windows автоматически входит в неё и запускает `Decanat.exe --kiosk` как оболочку. При входе сотрудника загружается его обычный рабочий стол. [Политика пользовательской оболочки](https://learn.microsoft.com/windows/client-management/mdm/policy-csp-admx-winlogon#customshell) · [Автоматический вход Windows](https://learn.microsoft.com/sysinternals/downloads/autologon)

1. В обычном режиме 2D задайте пароль сотрудника. Откройте PowerShell от имени администратора и выполните команду из каталога установленного приложения.

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows_kiosk_2d.ps1 -Action Enable -Installation (Get-Location).Path
   ```

2. Перезагрузите терминал. При первом входе нажмите **Ctrl+Alt+F10** (также **Ctrl+Alt+A** в латинской раскладке), войдите с паролем сотрудника, настройте Codex или API в профиле киоска, загрузите базу и проверьте аудиоустройства. Профиль киоска хранит собственные настройки и журнал. Для входа Codex завершите работу с браузером перед передачей терминала студентам.
3. Проверьте автоматический вход после перезагрузки, голосовой разговор и отсутствие рабочего стола при **Alt+F4**, **Win+E**, **Win+R**, **Ctrl+Esc** и **Ctrl+Shift+Esc**. Проверьте отдельный вход сотрудника через **Ctrl+Alt+Del** и «Сменить пользователя». Если Windows снова входит в киоск после выхода, удерживайте **Shift** при выходе, чтобы пропустить автоматический вход.

Для проверки конфигурации выполните скрипт с `-Action Status`. Для отключения выполните его от имени администратора с `-Action Disable` и завершите сеанс терминала. Данные профиля киоска сохраняются.

## Профиль киоска 3D на Windows 11

`Decanat.exe --kiosk` открывает аватар на весь экран и отключает игровую консоль. Ограничение доступа к ОС и автозапуск при входе обеспечиваются профилем Assigned Access. Скрипт `tools/windows_kiosk_profile.py` формирует XML для Windows 11 22H2+ и отдельной локальной учётной записи. Профиль разрешает запуск установленного EXE, встроенного Python, Unreal и Codex. В меню закрепляется только ярлык приложения. [Схема Assigned Access](https://learn.microsoft.com/windows/configuration/assigned-access/configuration-file)

1. На стенде создайте отдельного стандартного пользователя, сохранив доступ администратора. Разместите приложение в каталоге, где терминальная учётная запись имеет только чтение и исполнение. Данные и вход провайдера настройте в профиле терминального пользователя. Если нужен браузер для входа Codex, выполните вход до включения ограничений.
2. Создайте общий ярлык `%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\AI-decanat.lnk` на установленный `Decanat.exe` с аргументом `--kiosk`. Из рабочей копии с Python сформируйте конфигурацию с фактическим путём к установке.

   ```powershell
   python tools/windows_kiosk_profile.py 'C:\Program Files\AI-decanat' '.\DecanatKiosk' .runtime/assigned-access.xml
   ```

3. Примените XML на отдельном стенде через MDM или пакет подготовки Windows. Через WMI Bridge конфигурацию применяет администратор от LocalSystem. Для отката сохраните предыдущую конфигурацию и используйте тот же способ удаления или восстановления. [Установка и снятие Assigned Access](https://learn.microsoft.com/windows/configuration/assigned-access/configure-multi-app-kiosk)
4. Перезагрузите стенд и проверьте автоматический запуск после входа терминального пользователя, отсутствие доступа к посторонним приложениям, три кнопки студента, парольный вход сотрудника и выход из профиля. Если требуется автоматический вход после загрузки ОС, его настраивает администратор по политике организации.

Выпуск требует рабочего ZIP вуза, приёмки чистой Windows и проверки лицензий моделей. [Сторонние компоненты](third-party.md)
