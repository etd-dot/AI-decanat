#include "DecanatAvatar.h"
#include "DecanatClient.h"
#include "DecanatFaceAnimInstance.h"
#include "DecanatFaceRequest.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/SceneComponent.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Animation/Skeleton.h"
#include "Dom/JsonObject.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Base64.h"
#include "Async/Async.h"
#include "Engine/GameInstance.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/World.h"
#include "UObject/StrongObjectPtr.h"
#if !UE_BUILD_SHIPPING
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "TimerManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#endif
#if DECANAT_WITH_ACE
#include "ACERuntimeModule.h"
#include "ACEAudioCurveSourceComponent.h"
#include "ACEBlueprintLibrary.h"
#endif

ADecanatAvatar::ADecanatAvatar()
{
    // A movable scene root makes the map's placement/rotation effective when
    // spawning the MetaHuman. A bare AActor otherwise returns an identity pose.
    SetRootComponent(CreateDefaultSubobject<USceneComponent>(TEXT("Root")));
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.bStartWithTickEnabled = false;
}

void ADecanatAvatar::Fail(const FString& Message)
{
    ConfigurationIssue = Message;
    bConfigured = false;
    bBodyConfigured = false;
    Motion.Reset();
    TransitionAnimation = nullptr;
    bAcknowledgeTransition = false;
    SetActorTickEnabled(false);
    UE_LOG(LogTemp, Error, TEXT("Decanat avatar: %s"), *Message);
}

void ADecanatAvatar::BeginPlay()
{
    Super::BeginPlay();
    auto* GameInstance = GetGameInstance();
    Client = GameInstance ? GameInstance->GetSubsystem<UDecanatClient>() : nullptr;
    if (!Client) { Fail(TEXT("Не удалось подключить управление аватаром")); return; }
    GConfig->GetString(TEXT("Decanat"), TEXT("LocalFaceProvider"), FaceProvider, GGameIni);
    UClass* CharacterClass = CharacterAsset.Get();
    if (!CharacterClass) {
        FString Path;
        GConfig->GetString(TEXT("Decanat"), TEXT("AvatarClass"), Path, GGameIni);
        if (!Path.IsEmpty()) CharacterClass = LoadClass<AActor>(nullptr, *Path);
    }
    if (!CharacterClass) { Fail(TEXT("Не подключён персонаж MetaHuman")); return; }
    Character = GetWorld()->SpawnActor<AActor>(CharacterClass, GetActorTransform());
    if (!Character) { Fail(TEXT("Не удалось создать персонажа")); return; }
    // MetaHuman Blueprint construction can initialize its own scene root.
    // Apply the application's placement after construction has completed too.
    Character->SetActorTransform(GetActorTransform(), false, nullptr, ETeleportType::TeleportPhysics);
    TArray<USkeletalMeshComponent*> Meshes;
    Character->GetComponents(Meshes);
    for (auto* Mesh : Meshes) if (Mesh->GetFName() == FName(TEXT("Body"))) Body = Mesh;
    if (!Body || !Body->GetSkeletalMeshAsset()) {
        Fail(TEXT("У персонажа отсутствует настроенный компонент Body")); return;
    }
    for (const FName Key : {FName(TEXT("SitIdleAnimation")), FName(TEXT("StandAnimation")),
                           FName(TEXT("StandIdleAnimation")), FName(TEXT("SitAnimation")),
                           FName(TEXT("SpeakingIdleAnimation"))}) {
        UAnimSequence* Animation = AnimationAssets.FindRef(Key);
        bool bRequested = Animation != nullptr;
        if (!Animation) {
            FString Path;
            GConfig->GetString(TEXT("Decanat"), *Key.ToString(), Path, GGameIni);
            if (!Path.IsEmpty()) {
                bRequested = true;
                Animation = LoadObject<UAnimSequence>(nullptr, *Path);
            }
        }
        if (!bRequested && Key == FName(TEXT("SpeakingIdleAnimation"))) continue;
        if (!Animation || !FMath::IsFinite(Animation->GetPlayLength()) ||
            Animation->GetPlayLength() <= KINDA_SMALL_NUMBER || !Animation->GetSkeleton() ||
            !Animation->GetSkeleton()->IsCompatibleMesh(Body->GetSkeletalMeshAsset())) {
            Fail(FString::Printf(TEXT("Не подключена или несовместима анимация %s"), *Key.ToString()));
            return;
        }
        AnimationAssets.Add(Key, Animation);
    }
    bBodyConfigured = true;
    // Body must advance even when culled; wall-clock timers cannot confirm a finished pose.
    Body->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
    bool bFaceGraphConfigured = false;
    for (auto* Mesh : Meshes) {
        if (Mesh->GetFName() != FName(TEXT("Face")) ||
            !Mesh->GetAnimClass() || !Mesh->GetAnimClass()->IsChildOf(UDecanatFaceAnimInstance::StaticClass())) continue;
        bFaceGraphConfigured = true;
        // Creator's optimized assembly may share body poses without ticking the
        // face. A custom facial graph needs independent animation evaluation.
        Mesh->SetLeaderPoseComponent(nullptr);
        Mesh->bPauseAnims = false;
        Mesh->bNoSkeletonUpdate = false;
        Mesh->SetForceRefPose(false);
        Mesh->SetAllowAnimCurveEvaluation(true);
        Mesh->ResetAllowedAnimCurveEvaluation();
        Mesh->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        Mesh->SetComponentTickEnabled(true);
        Mesh->AddTickPrerequisiteComponent(Body);
    }
    Client->OnEvent.AddUObject(this, &ADecanatAvatar::Handle);
    PlayBody(TEXT("SitIdleAnimation"), true);
#if DECANAT_WITH_ACE
    const TSet<FString> LocalProviders = {
        TEXT("LocalA2F-Mark"), TEXT("LocalA2F-James"), TEXT("LocalA2F-Claire"),
        TEXT("LocalA2F-Mark-AR"), TEXT("LocalA2F-James-AR"), TEXT("LocalA2F-Claire-AR")
    };
    bConfigured = bFaceGraphConfigured && Character->FindComponentByClass<UACEAudioCurveSourceComponent>() &&
        LocalProviders.Contains(FaceProvider) &&
        UACEBlueprintLibrary::GetAvailableA2FProviderNames().Contains(FName(*FaceProvider));
    if (!bConfigured) ConfigurationIssue = TEXT("Не настроен локальный provider мимики ACE");
    if (bConfigured) FACERuntimeModule::Get().AllocateA2F3DResources(FName(*FaceProvider));
#else
    ConfigurationIssue = TEXT("Сборка аватара выполнена без локального ACE");
#endif
#if !UE_BUILD_SHIPPING
    bMotionTrace = FParse::Param(FCommandLine::Get(), TEXT("DecanatMotionTrace"));
    if (bMotionTrace) GetWorld()->GetTimerManager().SetTimer(
        MotionTraceTimer, this, &ADecanatAvatar::SampleMotion, 0.1f, true);
#endif
}

bool ADecanatAvatar::PlayBody(const FName& Key, bool bLoop)
{
    UAnimSequence* Animation = AnimationAssets.FindRef(Key);
    if (!bBodyConfigured || !Body || !Animation) return false;
    if (CurrentAnimation == Animation && bLoop && bCurrentLooping) return true;
    CurrentAnimation = Animation;
    bCurrentLooping = bLoop;
    Body->PlayAnimation(Animation, bLoop);
    // Initialize the visible pose immediately rather than showing the reference
    // A-pose until the first component tick (notably when entering the scene).
    Body->TickAnimation(0.0f, false);
    Body->RefreshBoneTransforms();
    return true;
}

void ADecanatAvatar::Transition(bool bStand, bool bAcknowledge)
{
    if (!bBodyConfigured) return;
    const bool bMoving = Motion.IsMoving();
    const bool bSameDirection = bStand
        ? Motion.GetPose() == FDecanatMotion::EPose::Rising
        : Motion.GetPose() == FDecanatMotion::EPose::Sitting;
    if (bMoving && bSameDirection) {
        // A repeated snapshot/action must not restart an in-flight clip.
        bAcknowledgeTransition = bAcknowledge;
        TransitionSession = Client ? Client->SessionId : FString();
        return;
    }
    if (!bMoving && Motion.IsStanding() == bStand) {
        // In particular, losing IPC while already seated must not play a sit
        // animation from its standing first frame.
        if (bAcknowledge && Client && Client->bConnected && !Client->SessionId.IsEmpty())
            Client->Send(TEXT("avatar_ready"));
        return;
    }
    float ReverseFraction = 0.0f;
    if (bMoving && TransitionAnimation) {
        if (auto* Player = Body->GetSingleNodeInstance()) {
            ReverseFraction = 1.0f - FMath::Clamp(
                Player->GetCurrentTime() / TransitionAnimation->GetPlayLength(), 0.0f, 1.0f);
        }
#if !UE_BUILD_SHIPPING
        TraceMotion(TEXT("before_reverse"));
#endif
    }
    TransitionTicket = Motion.Begin(bStand);
    bAcknowledgeTransition = bAcknowledge;
    TransitionSession = Client ? Client->SessionId : FString();
    const FName Key = bStand ? FName(TEXT("StandAnimation")) : FName(TEXT("SitAnimation"));
    if (!PlayBody(Key, false)) { Fail(TEXT("Не удалось запустить переход позы")); return; }
    TransitionAnimation = AnimationAssets.FindRef(Key);
    if (bMoving) {
        // The authored stand/sit tracks are exact reverses. Continue at the
        // corresponding pose instead of jumping to the opposite endpoint.
        Body->GetSingleNodeInstance()->SetPosition(ReverseFraction * TransitionAnimation->GetPlayLength(), false);
        Body->TickAnimation(0.0f, false);
        Body->RefreshBoneTransforms();
#if !UE_BUILD_SHIPPING
        TraceMotion(TEXT("after_reverse"));
#endif
    }
    SetActorTickEnabled(true);
}

void ADecanatAvatar::RefreshIdle()
{
    if (Motion.IsMoving()) return;
    FName Key(TEXT("SitIdleAnimation"));
    if (Motion.IsStanding()) {
        Key = ConversationState == TEXT("speaking") && AnimationAssets.Contains(TEXT("SpeakingIdleAnimation"))
            ? FName(TEXT("SpeakingIdleAnimation")) : FName(TEXT("StandIdleAnimation"));
    }
    PlayBody(Key, true);
}

void ADecanatAvatar::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bBodyConfigured || !Motion.IsMoving() || !TransitionAnimation) return;
    auto* Playback = Body->GetSingleNodeInstance();
    if (!Playback || Playback->GetCurrentAsset() != TransitionAnimation.Get()) {
        Fail(TEXT("Воспроизведение перехода позы было прервано")); return;
    }
    if (Playback->GetCurrentTime() + KINDA_SMALL_NUMBER < TransitionAnimation->GetPlayLength()) return;
    if (!Motion.Complete(TransitionTicket)) return;
    const bool bSendReady = bAcknowledgeTransition && Client && Client->bConnected &&
        !TransitionSession.IsEmpty() && TransitionSession == Client->SessionId;
#if !UE_BUILD_SHIPPING
    TraceMotion(TEXT("complete"), bSendReady);
#endif
    TransitionAnimation = nullptr;
    bAcknowledgeTransition = false;
    TransitionSession.Empty();
    SetActorTickEnabled(false);
    RefreshIdle();
    if (bSendReady) Client->Send(TEXT("avatar_ready"));
}

void ADecanatAvatar::Handle(const FString& Kind, const FString& Turn, const TSharedPtr<FJsonObject>& Payload)
{
    if (!Payload) return;
#if !UE_BUILD_SHIPPING
    TraceMotion(TEXT("event:") + Kind);
#endif
    if (Kind == TEXT("avatar")) {
        FString Action;
        if (!Payload->TryGetStringField(TEXT("action"), Action)) return;
        if (Action == TEXT("stand")) Transition(true, true);
        else if (Action == TEXT("sit")) Transition(false, true);
    } else if (Kind == TEXT("state") || Kind == TEXT("snapshot")) {
        const FString PreviousState = ConversationState;
        if (!Payload->TryGetStringField(TEXT("state"), ConversationState)) return;
        if (PreviousState == TEXT("speaking") && ConversationState != TEXT("speaking")) StopFace();
        if (Kind == TEXT("snapshot")) {
            if (ConversationState == TEXT("standing_up")) Transition(true, true);
            else if (ConversationState == TEXT("sitting_down")) Transition(false, true);
            else if (ConversationState == TEXT("idle")) {
                Motion.Reset();
                TransitionAnimation = nullptr;
                bAcknowledgeTransition = false;
                SetActorTickEnabled(false);
            } else if (!Motion.IsStanding() && !Motion.IsMoving()) Transition(true, false);
        }
        RefreshIdle();
    } else if (Kind == TEXT("playback_stop") || Kind == TEXT("cleared")) {
        StopFace();
        ConversationState = TEXT("listening");
        RefreshIdle();
    } else if (Kind == TEXT("connection_error")) {
        StopFace();
        ConversationState.Empty();
        Transition(false, false);
    } else if (Kind == TEXT("speech")) {
#if DECANAT_WITH_ACE
        if (!bConfigured || !Character) return;
        TArray<uint8> Bytes;
        if (!FBase64::Decode(Payload->GetStringField(TEXT("audio_b64")), Bytes) || Bytes.Num() % 2) return;
        const int32 Rate = Payload->GetIntegerField(TEXT("sample_rate"));
        auto* Consumer = Character->FindComponentByClass<UACEAudioCurveSourceComponent>();
        if (!Consumer) return;
        StopFace();
        FaceSession = Client ? Client->SessionId : FString();
        FaceTurn = Turn;
        Consumer->OnAnimationStarted.RemoveAll(this);
        Consumer->OnAnimationStarted.AddDynamic(this, &ADecanatAvatar::FaceStarted);
        const FName Provider(*FaceProvider);
        const uint64 Generation = ++FaceGeneration;
        TWeakObjectPtr<ADecanatAvatar> WeakThis(this);
        auto Request = MakeShared<FDecanatFaceRequest, ESPMode::ThreadSafe>(Consumer);
        FaceRequest = Request;
        // The audio device agent plays PCM. Disable ACE's audible playback in the configured character.
        AsyncTask(ENamedThreads::AnyBackgroundThreadNormalTask, [WeakThis, Request, Generation, Provider, Rate, Bytes=MoveTemp(Bytes)]() {
            if (Request->IsCancelled()) return;
            TArrayView<const int16> Samples(reinterpret_cast<const int16*>(Bytes.GetData()), Bytes.Num()/2);
            const bool bAccepted = FACERuntimeModule::Get().AnimateFromAudioSamples(&Request.Get(), Samples, 1, Rate, true, {}, nullptr, Provider);
            UE_LOG(LogTemp, Display, TEXT("Decanat local ACE generation accepted=%d provider=%s"), bAccepted, *Provider.ToString());
            if (!bAccepted) AsyncTask(ENamedThreads::GameThread, [WeakThis, Generation]() {
                if (!WeakThis.IsValid() || WeakThis->FaceGeneration.load() != Generation) return;
                WeakThis->bConfigured = false;
                WeakThis->ConfigurationIssue = TEXT("Локальная генерация мимики ACE недоступна. Проверьте модель и GPU");
            });
        });
#endif
    }
}

void ADecanatAvatar::StopFace()
{
    ++FaceGeneration;
    FaceSession.Empty(); FaceTurn.Empty();
#if DECANAT_WITH_ACE
    if (FaceRequest) {
        auto Cancelled = MoveTemp(FaceRequest);
        Cancelled->Cancel();
        // Detach this utterance's registry entry off the game thread. It can
        // never detach a newer utterance, which has a different consumer identity.
        AsyncTask(ENamedThreads::AnyBackgroundThreadNormalTask, [Cancelled] {
            FACERuntimeModule::Get().CancelAnimationGeneration(Cancelled.Get());
        });
    }
    if (Character) if (auto* Consumer = Character->FindComponentByClass<UACEAudioCurveSourceComponent>()) {
        // All calls on the actual component now run on this game thread.
        UACEBlueprintLibrary::StopCharacter(Character);
    }
#endif
}

void ADecanatAvatar::FaceStarted()
{
    const bool bCurrent = Client && Client->bConnected && !FaceSession.IsEmpty() && !FaceTurn.IsEmpty() &&
        FaceSession == Client->SessionId && FaceTurn == Client->TurnId;
#if !UE_BUILD_SHIPPING
    TraceMotion(TEXT("face_started"), bCurrent);
#endif
    if (bCurrent) Client->SendForTurn(TEXT("face_ready"), FaceSession, FaceTurn);
}

void ADecanatAvatar::EndPlay(const EEndPlayReason::Type Reason)
{
#if !UE_BUILD_SHIPPING
    GetWorld()->GetTimerManager().ClearTimer(MotionTraceTimer);
#endif
    if (Client) Client->OnEvent.RemoveAll(this);
    Motion.Reset();
    SetActorTickEnabled(false);
    TransitionAnimation = nullptr;
    bBodyConfigured = bConfigured = false;
    StopFace();
#if DECANAT_WITH_ACE
    if (!FaceProvider.IsEmpty()) FACERuntimeModule::Get().FreeA2F3DResources(FName(*FaceProvider));
#endif
    if (Character) Character->Destroy();
    Super::EndPlay(Reason);
}

#if !UE_BUILD_SHIPPING
void ADecanatAvatar::SampleMotion()
{
    TraceMotion(TEXT("sample"));
}

void ADecanatAvatar::TraceMotion(const FString& Phase, bool bReady) const
{
    if (!bMotionTrace) return;
    auto Data = MakeShared<FJsonObject>();
    auto* Player = Body ? Body->GetSingleNodeInstance() : nullptr;
    auto* Animation = Player ? Player->GetAnimationAsset() : nullptr;
    Data->SetStringField(TEXT("phase"), Phase);
    Data->SetNumberField(TEXT("time"), FPlatformTime::Seconds());
    Data->SetNumberField(TEXT("qpc_time"), FPlatformTime::ToSeconds64(FPlatformTime::Cycles64()));
    Data->SetNumberField(TEXT("pose"), static_cast<int32>(Motion.GetPose()));
    Data->SetStringField(TEXT("animation"), Animation ? Animation->GetName() : TEXT(""));
    Data->SetNumberField(TEXT("position"), Player ? Player->GetCurrentTime() : -1.0);
    Data->SetNumberField(TEXT("length"), Animation ? Animation->GetPlayLength() : 0.0);
    Data->SetStringField(TEXT("state"), ConversationState);
    Data->SetStringField(TEXT("session_id"), Client ? Client->SessionId : FString());
    Data->SetStringField(TEXT("turn_id"), Client ? Client->TurnId : FString());
    Data->SetStringField(TEXT("face_turn"), FaceTurn);
#if DECANAT_WITH_ACE
    if (Character) {
        if (auto* Consumer = Character->FindComponentByClass<UACEAudioCurveSourceComponent>())
            Data->SetNumberField(TEXT("face_media_position"), Consumer->GetAnimationPlaybackTimeForDiagnostics());
        TArray<USkeletalMeshComponent*> Meshes;
        Character->GetComponents(Meshes);
        for (auto* Mesh : Meshes) if (Mesh->GetFName() == FName(TEXT("Face"))) {
            auto* FaceAnimation = Mesh->GetPostProcessInstance();
            Data->SetNumberField(TEXT("face_blink_curve"), FaceAnimation ? FaceAnimation->GetCurveValue(TEXT("CTRL_expressions_eyeBlinkL")) : -1);
            auto* MainFaceAnimation = Mesh->GetAnimInstance();
            Data->SetNumberField(TEXT("face_jaw_curve"), MainFaceAnimation ? MainFaceAnimation->GetCurveValue(TEXT("CTRL_expressions_jawOpen")) : -1);
        }
    }
#endif
    Data->SetBoolField(TEXT("connected"), Client && Client->bConnected);
    Data->SetBoolField(TEXT("body_configured"), bBodyConfigured);
    Data->SetBoolField(TEXT("avatar_configured"), bConfigured);
    Data->SetBoolField(TEXT("acknowledge"), bReady);
    if (Body && (Phase == TEXT("before_reverse") || Phase == TEXT("after_reverse"))) {
        auto Bones = MakeShared<FJsonObject>();
        for (const FName Bone : {FName(TEXT("pelvis")), FName(TEXT("foot_l")), FName(TEXT("foot_r")),
                                FName(TEXT("lowerarm_l")), FName(TEXT("lowerarm_r")), FName(TEXT("head"))}) {
            const FVector Location = Body->GetBoneLocation(Bone);
            TArray<TSharedPtr<FJsonValue>> Coordinates;
            for (const double Value : {Location.X, Location.Y, Location.Z})
                Coordinates.Add(MakeShared<FJsonValueNumber>(Value));
            Bones->SetArrayField(Bone.ToString(), Coordinates);
        }
        Data->SetObjectField(TEXT("bones"), Bones);
    }
    FString Json;
    FJsonSerializer::Serialize(Data, TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Json));
    UE_LOG(LogTemp, Display, TEXT("DECANAT_MOTION %s"), *Json);
}
#endif
