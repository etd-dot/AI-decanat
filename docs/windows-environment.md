# Проверка Windows-ПК для F-01

Дата: 2026-11-04. Проверка выполнена непосредственно на ПК с копией проекта в `C:\Games\AI-decanat`. Это доступный стенд разработки; минимальные требования готового продукта ещё не измерены.

Обновление 2026-11-06: UE 5.6.1 + MetaHuman/Core Data + ACE 2.5/Mark 3.0 установлены. Собраны и запущены отдельные Win64 Development targets и packaged build с ACE; девять сценариев движений/отмены и реальные кривые лица проходят. Для устройств по умолчанию используется проверенный совместимый WASAPI. Native WebRTC DLL и все локальные модели речи/поиска установлены и проверены по SHA. Полный прогон — 86 passed, без пропусков; пользователь подтвердил голосовой вопрос и озвученный ответ в пробном окне. API-fallback выключен. Подробности и оставшиеся проверки — [progress.md](progress.md), инвентарь — `assets/manifest/windows-runtime-checks.json`. Исторические записи ниже сохраняют состояние на момент исходных проверок.

## Фактически обнаружено

| Компонент | Результат |
| --- | --- |
| ОС | Windows 11 Pro x64, `10.0.28000` |
| CPU | AMD Ryzen 5 3600, 6 ядер / 12 потоков |
| RAM | 16 GiB |
| GPU | NVIDIA GeForce RTX 3080, 10240 MiB VRAM |
| Драйвер | NVIDIA 610.62, проверен через `nvidia-smi` |
| Диск C: до установки инструментов | 522.3 GiB свободно из 931.4 GiB |
| Epic Games Launcher | Установлен; исходно `LauncherInstalled.dat` содержит пустой `InstallationList` |
| Unreal после установки | UE **5.6.1**, changelist `44394996`, `C:\Games\UE_5.6`; Core Data и пресеты MetaHuman присутствуют |
| Ассеты проекта | Созданы настоящие `Office.umap`, 8 материалов и редактируемый `MC_Decanat`; runtime Blueprint и анимации пока не подключены |

Проверены стандартные каталоги Epic, регистрация UE в HKLM/HKCU, `C:\Games`, `C:\gam` и документы пользователя. Каталоги кэша Unreal 4.x/5.x сами по себе не означают наличие движка. Произвольные внешние установки не исключены: для них передать `-EngineRoot`.

Для создания MetaHuman Epic рекомендует 32 GB RAM и существенно больше ядер, чем есть на этом ПК. Установленные 16 GiB — ограничение стенда, а не доказательство невозможности запуска. RTX 3080 соответствует указанному Epic классу GPU; достаточность общих ресурсов под MetaHuman, локальную мимику и backend проверяется отдельно. Замеры FPS и памяти ещё не выполнялись. См. [требования MetaHuman](https://dev.epicgames.com/documentation/metahuman/metahuman-hardware-requirements-in-unreal-engine?lang=en-US).

## Восстановление среды

- В `.tools` получены переносимые Git и uv из официальных GitHub-релизов; SHA-256 сверены с digest релизов. Системный PATH не изменялся.
- `uv sync --frozen --python 3.12` восстановил `.venv` и зависимости из существующего `uv.lock`. Дополнительные модели и speech-зависимости не загружались.
- Установлены подписанные Microsoft Visual Studio 2022 Build Tools `17.14.37827.9` в `C:\Games\BuildTools2022`: C++ workload, MSVC `14.38.33130` и `14.44.35207`, Windows SDK `10.0.22621.0`, CMake. Код установщика — 0, `vswhere` подтверждает `isComplete=true`. Переносимый C++-тест аватара реально собран и выполнен через MSVC 14.38.
- Исходные незакоммиченные изменения с Mac сохранены; коммит не выполнялся.

Первый прогон Python-тестов выявил реальные ошибки Windows: незакрытые SQLite-соединения, `fsync` файла без права записи и чтение UTF-8 через CP1251. Соединения теперь закрываются до замены базы; синхронизация выполняется через дескриптор с правом записи. Для текстовых конфигураций/ответов явно указана UTF-8. В тестах отмены ожидание 10–20 мс заменено подтверждением начала работы провайдера, чтобы проверки не зависели от скорости диска и планировщика.

Итоговый прогон с окружением MSVC 14.38: **66 passed, 2 skipped**, Ruff проходит. Пропущены интеграция с отсутствующей нативной WebRTC-библиотекой и проверка `pywhispercpp`, который ещё не установлен. `tools/unreal.ps1 -Action Build` корректно отказывается до сборки при отсутствующем движке; VS/MSVC/SDK обнаруживаются, единственная недостающая предпосылка — UE 5.6.

## Команды продолжения

Актуальное продолжение F-01: отдельный деловой `BP_DeanOffice` и четыре собственных клипа установлены в Office. Проверены 388 кадров, опора стоп, стыки и настоящий игровой цикл в PIE; размещение совпадает с предпросмотром. Редакторский и игровой targets без ACE успешно собраны, 70 тестов проходят, 2 пропущены. Внешность утверждена пользователем 2026-11-04, F-01 завершён. Воспроизводимые команды `PreviewMotion`/`VerifyMotion`, источники одежды и Blender — [avatar-authoring.md](avatar-authoring.md). Подробности ниже относятся к последовательным этапам подготовки базового MetaHuman.

Последний результат: **UE Optimized / High runtime-экспорт успешно завершён (код 0)**. Сохранены 102 ассета, 319.8 MiB; SHA-256 записаны в `assets/manifest/metahuman-runtime.json`. Новый процесс Unreal загрузил `BP_Decanat` с настоящими Body, Face и одеждой. Полный риг и texture sources сохранены. Это не проверка четырёх движений, окончательного костюма, ACE или packaged build.

Технический кадр персонажа в кабинете получен и просмотрен: `.runtime/unreal/metahuman-preview.png`, 1280×720. Для него явно вычислена совместимая neutral stand idle-поза; временный актор не сохраняется в карту. Действие `BindCharacter` отдельно сохранило настоящий Blueprint в Character Asset существующего DecanatAvatar в Office; `AvatarClass` заполнен. Исходные анимационные поля и LocalFaceProvider остаются пустыми, готовность приложения не заявляется. Команды повторения: `-Action PreviewCharacter -EditorCoreLimit 4` и `-Action BindCharacter`. Другая существующая привязка персонажа не перезаписывается.

Уточнение после входа Epic: авторизация прошла (`EOS_Success`), но первый импорт полного лицевого рига завершился OOM при запросе ещё 2340.2 MiB. Пользователь увеличил файл подкачки; фактически активны 16384 MiB с максимумом 32768 MiB. Повтор выполняется без `-Interactive`, чтобы не открывать дополнительное окно Character Asset. При повторяющемся TLS-сбое на reused connection проверен штатный флаг `-NoHttpConnectionReuse` (передаёт Unreal `-noreuseconn`, проверка сертификатов остаётся включённой). С ним сервис отдал DNA, и Interchange завершил импорт без прежней аварии. Завершение всего assembly проверяется отдельно по реальным ассетам.

Текущее состояние процесса и его код выхода записываются в `.runtime/unreal/AssembleCharacter-attempt.json`; `.runtime/unreal/metahuman.json` содержит время проверки и PID. После аварии редактора оставшийся `rig_pending` нельзя считать активным процессом. При коде выхода 0 обёртка также требует свежий отчёт об assembly.

Продолжение после установки движка: базовый `DecanatEditor Win64 Development` реально собран без ACE (код 0), Python commandlet UE создал и сохранил `/Game/Decanat/Office` (код 0). Дополнительно установлен .NET Framework 4.8 SDK: первый настоящий запуск UBT обнаружил его отсутствие, хотя MSVC/Windows SDK уже были готовы. Скрипт проверки теперь учитывает и эту зависимость. Движок автоматически находится рядом с репозиторием, даже если запись в Launcher отсутствует.

Редакторский модуль и зависимости MetaHuman затем также собраны успешно. Команда `PrepareCharacter` реально создала `MC_Decanat` с `valid_character=true`; его класс UE 5.6 не представлен отдельным Python-типом, поэтому проверяется настоящий UObject class path. Подготовка рига в полном редакторе ожидает авторизацию Epic владельцем (`authorization_pending`). Финальный прогон Python/native-проверок: **69 passed, 2 skipped**, Ruff проходит.

Из корня репозитория в PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action Check
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action Build
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action CreateScene
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action PrepareCharacter
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action Render
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action AssembleCharacter -Interactive -EditorCoreLimit 4
```

При нестандартном пути добавить, например, `-EngineRoot 'D:\Epic\UE_5.6'`. Поддерживается также `UE_ROOT`. Скрипт выбирает только UE 5.6, проверяет завершённую установку VS 2022, x64-компилятор и комплект SDK; наличие предпосылок не объявляется успешной компиляцией. Для 16 GiB RAM число параллельных задач сборки по умолчанию ограничено двумя, доступен `-MaxParallelActions`.

`Build` вызывает штатный `Build.bat` для `DecanatEditor Win64 Development` с `DECANAT_WITH_ACE=0`; прежнее значение переменной восстанавливается. `-DisableAdaptiveUnity` предотвращает компиляцию каждого доступного исходника MetaHuman отдельно из-за writable-атрибутов установки. `CreateScene` сначала собирает модуль, затем запускает Python commandlet Unreal и проверяет появление `Office.umap`; этот путь уже выполнен на UE 5.6.1. Существующая карта не перезаписывается. Журналы и отчёты сохраняются в игнорируемом `.runtime/unreal/`.

`PrepareCharacter` использует установленный настоящий пресет Epic Bruce и сохраняет редактируемый MetaHuman `MC_Decanat`; это ещё не готовый runtime Blueprint. `AssembleCharacter` запускает полный редактор и штатные операции Epic: face rig, texture sources 2K, assembly. Эти операции подготовки могут потребовать входа владельца в Epic; они не заменяют локальные речь/мимику рабочего приложения. Модуль `DecanatEditorTools` существует только в редакторе. Рендер из камеры карты создаётся действием `Render` в `.runtime/unreal/office.png`; рендер кабинета не является утверждением отсутствующего персонажа.

Рендер кабинета реально получен и просмотрен на D3D12, 1280×720. Выявленный пересвет устранён в карте и генераторе фиксированной экспозицией ISO 400 / 1/60 с / f/4; повторный кадр проверен. Для одновременного ожидания авторизации в другом редакторе рендер запускался с `-corelimit=2` (один shader worker). Для входа Epic использовать `AssembleCharacter -Interactive`: он открывает редактор реального Character Asset. Первый код входа истёк без подтверждения; интерактивный повтор ожидает владельца. Сборка персонажа запускается на пустой служебной карте, чтобы не блокировать сохранение Office.

В Epic Games Launcher установить **UE 5.6.x** с **MetaHuman Creator Core Data**, затем включить MetaHuman Creator в проекте и создать настоящий MetaHuman по [avatar-brief.md](avatar-brief.md). Порядок создания/сборки персонажа описан в [документации Epic](https://dev.epicgames.com/documentation/metahuman/metahuman-creator-in-unreal-engine). До выбора бесплатных одежды/волос/анимаций проверить условия конкретных материалов.

UE 5.8 не выбран: у текущего кандидата ACE 2.5 официально заявлены UE 5.5/5.6. Для UE 5.8 требуется отдельно выбрать и проверить совместимый локальный комплект, поэтому обновление версии не выполняется. См. [инструкцию ACE 2.5](https://docs.nvidia.com/ace/ace-unreal-plugin/2.5/ace-unreal-plugin-install.html) и [требования UE 5.6 к C++](https://dev.epicgames.com/documentation/en-us/unreal-engine/setting-up-visual-studio-development-environment-for-cplusplus-projects-in-unreal-engine?application_version=5.6).

Базовый Unreal-модуль собран, кабинет создан в движке; внешний вид персонажа и все его движения ещё не утверждены. F-01 остаётся открытым до настоящего MetaHuman с подключёнными ассетами и визуальной приёмки.
