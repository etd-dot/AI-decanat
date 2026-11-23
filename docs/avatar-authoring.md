# Сцена и движения настоящего MetaHuman

Утверждённый образец F-01: `/Game/Decanat/Characters/BP_DeanOffice` в `/Game/Decanat/Office`. Пользователь утвердил лицо, причёску, одежду и кабинет 2026-11-04. Исходный `MC_Decanat` и штатный результат сборки `BP_Decanat` сохранены отдельно. F-01 завершён; подтверждение относится к контрольным суммам показанного образца.

## Ассеты

- Основа: Epic Bruce, MetaHuman Creator Core Data из UE 5.6.1, полный лицевой риг и texture sources 2K, экспорт UE Optimized / High.
- Одежда: `/Game/Decanat/Wardrobe/office_v4/SKM_OfficeOutfit`, семь материалов; светлая рубашка с воротником, тёмный пиджак и брюки, закрытая обувь. Топология основы и исходные веса происходят из Epic Default Garment. Рукава, брючины, лацканы, воротник, детали и обувь доработаны скриптом проекта в Blender. Это производный материал Epic, а не независимо лицензированная модель тела.
- Источник одежды: `assets/source/avatar/office-outfit-office_v4.blend`; воспроизводимый генератор — `tools/author_avatar_outfit.py`.
- Движения: `/Game/Decanat/Animations/office_v5/AS_SitIdle`, `AS_Stand`, `AS_StandIdle`, `AS_Sit`. Idle длится 4 с, переходы — 2.4 с, частота — 30 кадров/с. Движения рассчитаны проектом по реальным костям MetaHuman; сторонние motion-клипы не скачивались.
- Генератор движений: `tools/author_avatar_motion.py`. Для переноса используются локальные ключи костей, без FBX-ретаргетинга анимаций. У каждого AnimSequence сохранён собственный Retarget Source Asset — настоящий Body mesh; общий скелет Epic не изменяется.

Одежда создаётся через MeshDescription и точную копию bind skeleton тела. Веса существующих поверхностей сохраняются; для новых деталей используются ближайшие поверхности исходного тела/одежды. При сохранении в Blueprint исходный MetaHuman не перезаписывается.

Базовое положение актора: `(45, 0, 0)` см, yaw `-90°`. Центр стула — x=10 см, верх сиденья — z=47 см; таз в сидячей позе — z=53 см. Стопы сохраняют опору при переходах. Свет в кабинете динамический, без редакторских меток непросчитанных теней.

## Повторение на этом Windows-ПК

В `tools/unreal.ps1` все действия сначала проверяют среду и собирают `DecanatEditor`: по умолчанию без ACE, с `-WithACE` — с установленным локальным плагином. Работа с неизменёнными версиями сохраняет существующие ассеты; для новой генерации требуется новая версия назначения, а не перезапись отредактированного контента.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action ExportAuthoring -EditorCoreLimit 4
& .tools/blender-4.5.15-windows-x64/blender.exe -b --factory-startup -t 4 --python-exit-code 1 --python tools/author_avatar_motion.py
& .tools/blender-4.5.15-windows-x64/blender.exe -b --factory-startup -t 4 --python-exit-code 1 --python tools/author_avatar_outfit.py
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action ImportMotion
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action ImportOutfit
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action PreviewMotion -EditorCoreLimit 4
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action VerifyMotion -EditorCoreLimit 4
```

`ExportAuthoring` использует полный D3D12-редактор: FBX-exporter требует render mesh и не подходит для NullRHI. Промежуточные FBX/JSON находятся в `.runtime/unreal/authoring/`. Blender 4.5.15 LTS установлен переносимо в `.tools`, его официальный ZIP проверен по SHA-256.

`InstallOfficeAvatar` используется только для первоначального создания отдельного Blueprint и его назначения в Office; повтор отказывается заменять существующий Blueprint. `BindMotion` обновляет ссылки на собственные сгенерированные клипы. Класс и все четыре анимации сохранены в карте и DefaultGame.ini, поэтому ссылки доступны cooker-у.

## Фактическая проверка

`VerifyMotion` оценивает все **388 кадров** в настоящем Unreal, проверяет опору стоп, максимальный шаг суставов между кадрами, стыки переходов и замыкание idle. Затем изолированный Play in Editor выполняет через `ADecanatAvatar` цикл `SitIdle → Stand → StandIdle → Sit → SitIdle`. Backend при этой проверке не подключается.

Последний результат: погрешность опоры стоп максимум **0.0131 см**, стыки проходят, игровое размещение совпадает с предпросмотром; PIE-цикл проходит. Отчёт — `.runtime/unreal/office-motion-verification.json`. Код выхода редактора 0 сам по себе не означает успех: обёртка требует свежий JSON с обоими положительными результатами.

Исправлены отсутствие scene root у актора приложения и стартовый кадр в A-позе: размещение сериализуется, сидячая поза вычисляется до первого отображения. Отдельно успешно собраны `DecanatEditor Win64 Development` и `Decanat Win64 Development`, оба **без ACE**. Игровой target скомпилирован; cooked/packaged-сборка этим не проверена.

Настоящие утверждённые кадры: `assets/preview/f01/standing.png`, `seated.png`, `rising.png`, `sitting.png`. Инвентарь исходников/ассетов, подтверждение пользователя и SHA-256 — `assets/manifest/office-avatar.json`. Изменённые в будущем варианты не получают утверждение автоматически.

## Лицо и локальный ACE — F-02/F-03

Установлены `NV_ACE_Reference` 2.5 и `NvAudio2FaceMark` 2.4 для UE 5.6. Runtime использует только `LocalA2F-Mark`; слышимым воспроизведением владеет backend, `ACE Audio Curve Source.Volume = 0`. Отдельные права на production-модель ещё проверяются — [third-party.md](third-party.md).

В собственном `ABP_DecanatFace` применяется `CopyPoseFromMesh` с явным Body source → `ModifyCurve` для моргания → `Apply ACE Face Animations` → `PoseBlend` с собственным `PA_DecanatArkit`. Face тикает независимо от LeaderPose и после Body, а штатный postprocess MetaHuman выполняет RigLogic. Исходные ассеты Epic не меняются. Mapping скопирован в проект, привязан к настоящему skeleton UE 5.6; старая editor-only SourceAnimation отвязана, добавлен точный CoreRedirect старого skeleton-пути. При программном создании графа обязательна регистрация узлов через `PostPlacedNewNode`, иначе Blueprint bindings остаются нулевыми.

`UDecanatFaceAnimInstance` задаёт сдержанное моргание раз в 3.2–6 секунд. Проверка измеряет не только значения кривых, но и реальные кости век относительно головы. Результат повторного прогона: кривая **0.797**, смещение века **0.894 см**.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action InspectFace -WithACE -EditorCoreLimit 4
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action VerifyBlink -WithACE -EditorCoreLimit 4
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action VerifyFace -WithACE -EditorCoreLimit 4
powershell -NoProfile -ExecutionPolicy Bypass -File tools/unreal.ps1 -Action Package -WithACE -EditorCoreLimit 4
& .venv/Scripts/python.exe tools/verify_packaged_motion.py .runtime/unreal/package-with-ace/Windows/Decanat/Binaries/Win64/Decanat.exe --real-face --play-output --cancel-preparation --output .runtime/unreal/packaged-cold-cancel-verification.json
```

`InstallFace -WithACE` применяется только при первоначальном создании и отказывается заменять существующий граф. `RepairFace -WithACE` повторно настраивает собственный graph/mapping, регистрирует bindings и включает blend-out к нейтральной позе; внешний вид утверждённого персонажа не пересоздаёт. `VerifyBlink`/`VerifyFace` требуют свежий JSON с положительным результатом, код выхода редактора сам по себе недостаточен.

В штатном IPC событие `speech` подготавливает мимику, native `face_ready` разрешает backend-событие `playback_start`. До подтверждения звук не поступает на устройство. У каждой реплики отдельный ACE consumer-посредник; callback-и копируются в очередь игрового потока, отменённая очередь отбрасывается. Старая отмена не может отсоединить новую реплику. Подробности — [protocol.md](protocol.md).

Локальные отчёты содержат точные границы испытаний. Открыты проверка настоящего микрофона/акустического перебивания, длительный прогон и окончательная поставка. Общая готовность продукта остаётся false.
