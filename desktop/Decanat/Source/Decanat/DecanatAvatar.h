#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "DecanatMotion.h"
#include <atomic>
#include "DecanatAvatar.generated.h"

class UDecanatClient;
class USkeletalMeshComponent;
class UAnimSequence;
class FJsonObject;
class FDecanatFaceRequest;

UCLASS()
class DECANAT_API ADecanatAvatar : public AActor
{
    GENERATED_BODY()
public:
    ADecanatAvatar();
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    bool IsConfigured() const { return bConfigured; }
    const FString& GetConfigurationIssue() const { return ConfigurationIssue; }

    // References saved in the map make these assets visible to the packaged-build cooker.
    // DefaultGame.ini remains a fallback for existing scenes.
    UPROPERTY(EditAnywhere, Category="Decanat|Assets") TSubclassOf<AActor> CharacterAsset;
    UPROPERTY(EditAnywhere, Category="Decanat|Assets") TMap<FName, TObjectPtr<UAnimSequence>> AnimationAssets;
private:
    UPROPERTY() TObjectPtr<AActor> Character;
    UPROPERTY() TObjectPtr<USkeletalMeshComponent> Body;
    UPROPERTY() TObjectPtr<UDecanatClient> Client;
    FString FaceProvider;
    FString ConfigurationIssue;
    FString TransitionSession;
    FString ConversationState;
    FString FaceSession, FaceTurn;
    FDecanatMotion Motion;
    uint64 TransitionTicket = 0;
    bool bAcknowledgeTransition = false;
    bool bBodyConfigured = false;
    bool bCurrentLooping = false;
    bool bConfigured = false;
    std::atomic<uint64> FaceGeneration{0};
#if DECANAT_WITH_ACE
    TSharedPtr<FDecanatFaceRequest, ESPMode::ThreadSafe> FaceRequest;
#endif
    UPROPERTY() TObjectPtr<UAnimSequence> TransitionAnimation;
    UPROPERTY() TObjectPtr<UAnimSequence> CurrentAnimation;
    void Handle(const FString& Kind, const FString& Turn, const TSharedPtr<FJsonObject>& Payload);
    bool PlayBody(const FName& Key, bool bLoop);
    void Transition(bool bStand, bool bAcknowledge);
    void RefreshIdle();
    void Fail(const FString& Message);
    void StopFace();
    UFUNCTION() void FaceStarted();
#if !UE_BUILD_SHIPPING
    // Opt-in, read-only evidence from the packaged Development build.
    FTimerHandle MotionTraceTimer;
    bool bMotionTrace = false;
    void SampleMotion();
    void TraceMotion(const FString& Phase, bool bReady = false) const;
#endif
};
